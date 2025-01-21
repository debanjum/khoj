import concurrent.futures
import json
import logging
import math
import os
import threading
import time
import uuid
from typing import Any, Callable, List, Optional, Set, Union

import cron_descriptor
import openai
import pytz
from apscheduler.job import Job
from apscheduler.triggers.cron import CronTrigger
from asgiref.sync import sync_to_async
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.requests import Request
from fastapi.responses import Response
from starlette.authentication import has_required_scope, requires

from khoj.configure import initialize_content
from khoj.database import adapters
from khoj.database.adapters import (
    AgentAdapters,
    AutomationAdapters,
    ConversationAdapters,
    EntryAdapters,
    get_default_search_model,
    get_user_photo,
)
from khoj.database.models import Agent, ChatModel, KhojUser, SpeechToTextModelOptions
from khoj.processor.conversation import prompts
from khoj.processor.conversation.anthropic.anthropic_chat import (
    extract_questions_anthropic,
)
from khoj.processor.conversation.google.gemini_chat import extract_questions_gemini
from khoj.processor.conversation.offline.chat_model import extract_questions_offline
from khoj.processor.conversation.offline.whisper import transcribe_audio_offline
from khoj.processor.conversation.openai.gpt import extract_questions
from khoj.processor.conversation.openai.whisper import transcribe_audio
from khoj.processor.conversation.utils import clean_json, defilter_query
from khoj.routers.helpers import (
    ApiUserRateLimiter,
    ChatEvent,
    CommonQueryParams,
    ConversationCommandRateLimiter,
    get_user_config,
    schedule_automation,
    schedule_query,
    update_telemetry_state,
)
from khoj.search_filter.date_filter import DateFilter
from khoj.search_filter.file_filter import FileFilter
from khoj.search_filter.word_filter import WordFilter
from khoj.search_type import text_search
from khoj.utils import state
from khoj.utils.config import OfflineChatProcessorModel
from khoj.utils.helpers import ConversationCommand, is_none_or_empty, timer
from khoj.utils.rawconfig import LocationData, SearchResponse
from khoj.utils.state import SearchType

# Initialize Router
api = APIRouter()
logger = logging.getLogger(__name__)
conversation_command_rate_limiter = ConversationCommandRateLimiter(
    trial_rate_limit=2, subscribed_rate_limit=100, slug="command"
)


@api.get("/search", response_model=List[SearchResponse])
@requires(["authenticated"])
async def search(
    q: str,
    request: Request,
    common: CommonQueryParams,
    n: Optional[int] = 5,
    t: Optional[SearchType] = SearchType.All,
    r: Optional[bool] = False,
    max_distance: Optional[Union[float, None]] = None,
    dedupe: Optional[bool] = True,
):
    user = request.user.object

    results = await execute_search(
        user=user,
        q=q,
        n=n,
        t=t,
        r=r,
        max_distance=max_distance or math.inf,
        dedupe=dedupe,
    )

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="search",
        **common.__dict__,
    )

    return results


async def execute_search(
    user: KhojUser,
    q: str,
    n: Optional[int] = 5,
    t: Optional[SearchType] = SearchType.All,
    r: Optional[bool] = False,
    max_distance: Optional[Union[float, None]] = None,
    dedupe: Optional[bool] = True,
    agent: Optional[Agent] = None,
):
    # Run validation checks
    results: List[SearchResponse] = []

    start_time = time.time()

    # Ensure the agent, if present, is accessible by the user
    if user and agent and not await AgentAdapters.ais_agent_accessible(agent, user):
        logger.error(f"Agent {agent.slug} is not accessible by user {user}")
        return results

    if q is None or q == "":
        logger.warning(f"No query param (q) passed in API call to initiate search")
        return results

    # initialize variables
    user_query = q.strip()
    results_count = n or 5
    search_futures: List[concurrent.futures.Future] = []

    # return cached results, if available
    if user:
        query_cache_key = f"{user_query}-{n}-{t}-{r}-{max_distance}-{dedupe}"
        if query_cache_key in state.query_cache[user.uuid]:
            logger.debug(f"Return response from query cache")
            return state.query_cache[user.uuid][query_cache_key]

    # Encode query with filter terms removed
    defiltered_query = user_query
    for filter in [DateFilter(), WordFilter(), FileFilter()]:
        defiltered_query = filter.defilter(defiltered_query)

    encoded_asymmetric_query = None
    if t != SearchType.Image:
        with timer("Encoding query took", logger=logger):
            search_model = await sync_to_async(get_default_search_model)()
            encoded_asymmetric_query = state.embeddings_model[search_model.name].embed_query(defiltered_query)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        if t in [
            SearchType.All,
            SearchType.Org,
            SearchType.Markdown,
            SearchType.Github,
            SearchType.Notion,
            SearchType.Plaintext,
            SearchType.Pdf,
        ]:
            # query markdown notes
            search_futures += [
                executor.submit(
                    text_search.query,
                    user_query,
                    user,
                    t,
                    question_embedding=encoded_asymmetric_query,
                    max_distance=max_distance,
                    agent=agent,
                )
            ]

        # Query across each requested content types in parallel
        with timer("Query took", logger):
            for search_future in concurrent.futures.as_completed(search_futures):
                hits = await search_future.result()
                # Collate results
                results += text_search.collate_results(hits, dedupe=dedupe)

                # Sort results across all content types and take top results
                results = text_search.rerank_and_sort_results(
                    results, query=defiltered_query, rank_results=r, search_model_name=search_model.name
                )[:results_count]

    # Cache results
    if user:
        state.query_cache[user.uuid][query_cache_key] = results

    end_time = time.time()
    logger.debug(f"🔍 Search took: {end_time - start_time:.3f} seconds")

    return results


@api.get("/update")
@requires(["authenticated"])
def update(
    request: Request,
    common: CommonQueryParams,
    t: Optional[SearchType] = None,
    force: Optional[bool] = False,
):
    user = request.user.object
    if not state.config:
        error_msg = f"🚨 Khoj is not configured.\nConfigure it via http://localhost:42110/settings, plugins or by editing {state.config_file}."
        logger.warning(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
    try:
        initialize_content(user=user, regenerate=force, search_type=t)
    except Exception as e:
        error_msg = f"🚨 Failed to update server via API: {e}"
        logger.error(error_msg, exc_info=True)
        raise HTTPException(status_code=500, detail=error_msg)
    else:
        components = []
        if state.search_models:
            components.append("Search models")
        components_msg = ", ".join(components)
        logger.info(f"📪 {components_msg} updated via API")

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="update",
        **common.__dict__,
    )

    return {"status": "ok", "message": "khoj reloaded"}


@api.post("/transcribe")
@requires(["authenticated"])
async def transcribe(
    request: Request,
    common: CommonQueryParams,
    file: UploadFile = File(...),
    rate_limiter_per_minute=Depends(
        ApiUserRateLimiter(requests=20, subscribed_requests=20, window=60, slug="transcribe_minute")
    ),
    rate_limiter_per_day=Depends(
        ApiUserRateLimiter(requests=60, subscribed_requests=600, window=60 * 60 * 24, slug="transcribe_day")
    ),
):
    user: KhojUser = request.user.object
    audio_filename = f"{user.uuid}-{str(uuid.uuid4())}.webm"
    user_message: str = None

    # If the file is too large, return an unprocessable entity error
    if file.size > 10 * 1024 * 1024:
        logger.warning(f"Audio file too large to transcribe. Audio file size: {file.size}. Exceeds 10Mb limit.")
        return Response(content="Audio size larger than 10Mb limit", status_code=422)

    # Transcribe the audio from the request
    try:
        # Store the audio from the request in a temporary file
        audio_data = await file.read()
        with open(audio_filename, "wb") as audio_file_writer:
            audio_file_writer.write(audio_data)
        audio_file = open(audio_filename, "rb")

        # Send the audio data to the Whisper API
        speech_to_text_config = await ConversationAdapters.get_speech_to_text_config()
        if not speech_to_text_config:
            # If the user has not configured a speech to text model, return an unsupported on server error
            status_code = 501
        elif speech_to_text_config.model_type == SpeechToTextModelOptions.ModelType.OFFLINE:
            speech2text_model = speech_to_text_config.model_name
            user_message = await transcribe_audio_offline(audio_filename, speech2text_model)
        elif speech_to_text_config.model_type == SpeechToTextModelOptions.ModelType.OPENAI:
            speech2text_model = speech_to_text_config.model_name
            if speech_to_text_config.ai_model_api:
                api_key = speech_to_text_config.ai_model_api.api_key
                api_base_url = speech_to_text_config.ai_model_api.api_base_url
                openai_client = openai.OpenAI(api_key=api_key, base_url=api_base_url)
            elif state.openai_client:
                openai_client = state.openai_client
            if openai_client:
                user_message = await transcribe_audio(audio_file, speech2text_model, client=openai_client)
            else:
                status_code = 501
    finally:
        # Close and Delete the temporary audio file
        audio_file.close()
        os.remove(audio_filename)

    if user_message is None:
        return Response(status_code=status_code or 500)

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="transcribe",
        **common.__dict__,
    )

    # Return the spoken text
    content = json.dumps({"text": user_message})
    return Response(content=content, media_type="application/json", status_code=200)


@api.get("/settings", response_class=Response)
@requires(["authenticated"])
def get_settings(request: Request, detailed: Optional[bool] = False) -> Response:
    user = request.user.object
    user_config = get_user_config(user, request, is_detailed=detailed)
    del user_config["request"]

    # Return config data as a JSON response
    return Response(content=json.dumps(user_config), media_type="application/json", status_code=200)


@api.patch("/user/name", status_code=200)
@requires(["authenticated"])
def set_user_name(
    request: Request,
    name: str,
    client: Optional[str] = None,
):
    user = request.user.object

    split_name = name.split(" ")

    if len(split_name) > 2:
        raise HTTPException(status_code=400, detail="Name must be in the format: Firstname Lastname")

    if len(split_name) == 1:
        first_name = split_name[0]
        last_name = ""
    else:
        first_name, last_name = split_name[0], split_name[-1]

    adapters.set_user_name(user, first_name, last_name)

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="set_user_name",
        client=client,
    )

    return {"status": "ok"}


async def extract_references_and_questions(
    request: Request,
    meta_log: dict,
    q: str,
    n: int,
    d: float,
    conversation_id: str,
    conversation_commands: List[ConversationCommand] = [ConversationCommand.Default],
    location_data: LocationData = None,
    send_status_func: Optional[Callable] = None,
    query_images: Optional[List[str]] = None,
    previous_inferred_queries: Set = set(),
    agent: Agent = None,
    query_files: str = None,
    tracer: dict = {},
):
    user = request.user.object if request.user.is_authenticated else None

    # Initialize Variables
    compiled_references: List[dict[str, str]] = []
    inferred_queries: List[str] = []

    agent_has_entries = False

    if agent:
        agent_has_entries = await sync_to_async(EntryAdapters.agent_has_entries)(agent=agent)

    if (
        not ConversationCommand.Notes in conversation_commands
        and not ConversationCommand.Default in conversation_commands
        and not agent_has_entries
    ):
        yield compiled_references, inferred_queries, q
        return

    # If Notes or Default is not in the conversation command, then the search should be restricted to the agent's knowledge base
    should_limit_to_agent_knowledge = (
        ConversationCommand.Notes not in conversation_commands
        and ConversationCommand.Default not in conversation_commands
    )

    if not await sync_to_async(EntryAdapters.user_has_entries)(user=user):
        if not agent_has_entries:
            logger.debug("No documents in knowledge base. Use a Khoj client to sync and chat with your docs.")
            yield compiled_references, inferred_queries, q
            return

    # Extract filter terms from user message
    defiltered_query = defilter_query(q)
    filters_in_query = q.replace(defiltered_query, "").strip()
    conversation = await sync_to_async(ConversationAdapters.get_conversation_by_id)(conversation_id)

    if not conversation:
        logger.error(f"Conversation with id {conversation_id} not found when extracting references.")
        yield compiled_references, inferred_queries, defiltered_query
        return

    filters_in_query += " ".join([f'file:"{filter}"' for filter in conversation.file_filters])
    using_offline_chat = False
    if is_none_or_empty(filters_in_query):
        logger.debug(f"Filters in query: {filters_in_query}")

    personality_context = prompts.personality_context.format(personality=agent.personality) if agent else ""

    # Infer search queries from user message
    with timer("Extracting search queries took", logger):
        # If we've reached here, either the user has enabled offline chat or the openai model is enabled.
        chat_model = await ConversationAdapters.aget_default_chat_model(user)
        vision_enabled = chat_model.vision_enabled

        if chat_model.model_type == ChatModel.ModelType.OFFLINE:
            using_offline_chat = True
            chat_model_name = chat_model.name
            max_tokens = chat_model.max_prompt_size
            if state.offline_chat_processor_config is None:
                state.offline_chat_processor_config = OfflineChatProcessorModel(chat_model_name, max_tokens)

            loaded_model = state.offline_chat_processor_config.loaded_model

            inferred_queries = extract_questions_offline(
                defiltered_query,
                model=chat_model,
                loaded_model=loaded_model,
                conversation_log=meta_log,
                should_extract_questions=True,
                location_data=location_data,
                user=user,
                max_prompt_size=chat_model.max_prompt_size,
                personality_context=personality_context,
                query_files=query_files,
                tracer=tracer,
            )
        elif chat_model.model_type == ChatModel.ModelType.OPENAI:
            api_key = chat_model.ai_model_api.api_key
            base_url = chat_model.ai_model_api.api_base_url
            chat_model_name = chat_model.name
            inferred_queries = extract_questions(
                defiltered_query,
                model=chat_model_name,
                api_key=api_key,
                api_base_url=base_url,
                conversation_log=meta_log,
                location_data=location_data,
                user=user,
                query_images=query_images,
                vision_enabled=vision_enabled,
                personality_context=personality_context,
                query_files=query_files,
                tracer=tracer,
            )
        elif chat_model.model_type == ChatModel.ModelType.ANTHROPIC:
            api_key = chat_model.ai_model_api.api_key
            chat_model_name = chat_model.name
            inferred_queries = extract_questions_anthropic(
                defiltered_query,
                query_images=query_images,
                model=chat_model_name,
                api_key=api_key,
                conversation_log=meta_log,
                location_data=location_data,
                user=user,
                vision_enabled=vision_enabled,
                personality_context=personality_context,
                query_files=query_files,
                tracer=tracer,
            )
        elif chat_model.model_type == ChatModel.ModelType.GOOGLE:
            api_key = chat_model.ai_model_api.api_key
            chat_model_name = chat_model.name
            inferred_queries = extract_questions_gemini(
                defiltered_query,
                query_images=query_images,
                model=chat_model_name,
                api_key=api_key,
                conversation_log=meta_log,
                location_data=location_data,
                max_tokens=chat_model.max_prompt_size,
                user=user,
                vision_enabled=vision_enabled,
                personality_context=personality_context,
                query_files=query_files,
                tracer=tracer,
            )

    # Collate search results as context for GPT
    inferred_queries = list(set(inferred_queries) - previous_inferred_queries)
    with timer("Searching knowledge base took", logger):
        search_results = []
        logger.info(f"🔍 Searching knowledge base with queries: {inferred_queries}")
        if send_status_func:
            inferred_queries_str = "\n- " + "\n- ".join(inferred_queries)
            async for event in send_status_func(f"**Searching Documents for:** {inferred_queries_str}"):
                yield {ChatEvent.STATUS: event}
        for query in inferred_queries:
            n_items = min(n, 3) if using_offline_chat else n
            search_results.extend(
                await execute_search(
                    user if not should_limit_to_agent_knowledge else None,
                    f"{query} {filters_in_query}",
                    n=n_items,
                    t=SearchType.All,
                    r=True,
                    max_distance=d,
                    dedupe=False,
                    agent=agent,
                )
            )
        search_results = text_search.deduplicated_search_responses(search_results)
        compiled_references = [
            {"query": q, "compiled": item.additional["compiled"], "file": item.additional["file"]}
            for q, item in zip(inferred_queries, search_results)
        ]

    yield compiled_references, inferred_queries, defiltered_query


@api.get("/health", response_class=Response)
@requires(["authenticated"], status_code=200)
def health_check(request: Request) -> Response:
    response_obj = {"email": request.user.object.email}
    return Response(content=json.dumps(response_obj), media_type="application/json", status_code=200)


@api.get("/v1/user", response_class=Response)
@requires(["authenticated"])
def user_info(request: Request) -> Response:
    # Get user information
    user: KhojUser = request.user.object
    user_picture = get_user_photo(user=user)
    is_active = has_required_scope(request, ["premium"])
    has_documents = EntryAdapters.user_has_entries(user=user)

    # Collect user information in a dictionary
    user_info = {
        "email": user.email,
        "username": user.username,
        "photo": user_picture,
        "is_active": is_active,
        "has_documents": has_documents,
        "khoj_version": state.khoj_version,
    }

    # Return user information as a JSON response
    return Response(content=json.dumps(user_info), media_type="application/json", status_code=200)


@api.get("/automations", response_class=Response)
@requires(["authenticated"])
def get_automations(request: Request) -> Response:
    user: KhojUser = request.user.object

    # Collate all automations created by user that are still active
    automations_info = [automation_info for automation_info in AutomationAdapters.get_automations_metadata(user)]

    # Return tasks information as a JSON response
    return Response(content=json.dumps(automations_info), media_type="application/json", status_code=200)


@api.delete("/automation", response_class=Response)
@requires(["authenticated"])
def delete_automation(request: Request, automation_id: str) -> Response:
    user: KhojUser = request.user.object

    try:
        automation_info = AutomationAdapters.delete_automation(user, automation_id)
    except ValueError:
        return Response(status_code=204)

    # Return deleted automation information as a JSON response
    return Response(content=json.dumps(automation_info), media_type="application/json", status_code=200)


@api.post("/automation", response_class=Response)
@requires(["authenticated"])
def post_automation(
    request: Request,
    q: str,
    crontime: str,
    subject: Optional[str] = None,
    city: Optional[str] = None,
    region: Optional[str] = None,
    country: Optional[str] = None,
    timezone: Optional[str] = None,
) -> Response:
    user: KhojUser = request.user.object

    # Perform validation checks
    if is_none_or_empty(q) or is_none_or_empty(crontime):
        return Response(content="A query and crontime is required", status_code=400)
    if not cron_descriptor.get_description(crontime):
        return Response(content="Invalid crontime", status_code=400)

    # Infer subject, query to run
    _, query_to_run, generated_subject = schedule_query(q, conversation_history={}, user=user)
    subject = subject or generated_subject

    # Normalize query parameters
    # Add /automated_task prefix to query if not present
    query_to_run = query_to_run.strip()
    if not query_to_run.startswith("/automated_task"):
        query_to_run = f"/automated_task {query_to_run}"

    # Normalize crontime for AP Scheduler CronTrigger
    crontime = crontime.strip()
    if len(crontime.split(" ")) > 5:
        # Truncate crontime to 5 fields
        crontime = " ".join(crontime.split(" ")[:5])

    # Convert crontime to standard unix crontime
    crontime = crontime.replace("?", "*")

    # Disallow minute level automation recurrence
    minute_value = crontime.split(" ")[0]
    if not minute_value.isdigit():
        return Response(
            content="Minute level recurrence is unsupported. Please create a less frequent schedule.",
            status_code=400,
        )

    # Create new Conversation Session associated with this new task
    title = f"Automation: {subject}"
    conversation = ConversationAdapters.create_conversation_session(user, request.user.client_app, title=title)

    # Schedule automation with query_to_run, timezone, subject directly provided by user
    try:
        # Use the query to run as the scheduling request if the scheduling request is unset
        calling_url = request.url.replace(query=f"{request.url.query}")
        automation = schedule_automation(
            query_to_run, subject, crontime, timezone, q, user, calling_url, str(conversation.id)
        )
    except Exception as e:
        logger.error(f"Error creating automation {q} for {user.email}: {e}", exc_info=True)
        return Response(
            content=f"Unable to create automation. Ensure the automation doesn't already exist.",
            media_type="text/plain",
            status_code=500,
        )

    # Collate info about the created user automation
    automation_info = AutomationAdapters.get_automation_metadata(user, automation)

    # Return information about the created automation as a JSON response
    return Response(content=json.dumps(automation_info), media_type="application/json", status_code=200)


@api.post("/trigger/automation", response_class=Response)
@requires(["authenticated"])
def trigger_manual_job(
    request: Request,
    automation_id: str,
):
    user: KhojUser = request.user.object

    # Check, get automation to edit
    try:
        automation: Job = AutomationAdapters.get_automation(user, automation_id)
    except ValueError as e:
        logger.error(f"Error triggering automation {automation_id} for {user.email}: {e}", exc_info=True)
        return Response(content="Invalid automation", status_code=403)

    # Trigger the job without waiting for the result.
    scheduled_chat_func = automation.func

    # Run the function in a separate thread
    thread = threading.Thread(target=scheduled_chat_func, args=automation.args, kwargs=automation.kwargs)
    thread.start()

    return Response(content="Automation triggered", status_code=200)


@api.put("/automation", response_class=Response)
@requires(["authenticated"])
def edit_job(
    request: Request,
    automation_id: str,
    q: Optional[str],
    subject: Optional[str],
    crontime: Optional[str],
    city: Optional[str] = None,
    region: Optional[str] = None,
    country: Optional[str] = None,
    timezone: Optional[str] = None,
) -> Response:
    user: KhojUser = request.user.object

    # Perform validation checks
    if is_none_or_empty(q) or is_none_or_empty(subject) or is_none_or_empty(crontime):
        return Response(content="A query, subject and crontime is required", status_code=400)
    if not cron_descriptor.get_description(crontime):
        return Response(content="Invalid crontime", status_code=400)

    # Check, get automation to edit
    try:
        automation: Job = AutomationAdapters.get_automation(user, automation_id)
    except ValueError as e:
        logger.error(f"Error editing automation {automation_id} for {user.email}: {e}", exc_info=True)
        return Response(content="Invalid automation", status_code=403)

    # Infer subject, query to run
    _, query_to_run, _ = schedule_query(q, conversation_history={}, user=user)
    subject = subject

    # Normalize query parameters
    # Add /automated_task prefix to query if not present
    query_to_run = query_to_run.strip()
    if not query_to_run.startswith("/automated_task"):
        query_to_run = f"/automated_task {query_to_run}"
    # Normalize crontime for AP Scheduler CronTrigger
    crontime = crontime.strip()
    if len(crontime.split(" ")) > 5:
        # Truncate crontime to 5 fields
        crontime = " ".join(crontime.split(" ")[:5])
    # Convert crontime to standard unix crontime
    crontime = crontime.replace("?", "*")

    # Disallow minute level automation recurrence
    minute_value = crontime.split(" ")[0]
    if not minute_value.isdigit():
        return Response(
            content="Recurrence of every X minutes is unsupported. Please create a less frequent schedule.",
            status_code=400,
        )

    # Construct updated automation metadata
    automation_metadata: dict[str, str] = json.loads(clean_json(automation.name))
    automation_metadata["scheduling_request"] = q
    automation_metadata["query_to_run"] = query_to_run
    automation_metadata["subject"] = subject.strip()
    automation_metadata["crontime"] = crontime
    conversation_id = automation_metadata.get("conversation_id")

    if not conversation_id:
        title = f"Automation: {subject}"

        # Create new Conversation Session associated with this new task
        conversation = ConversationAdapters.create_conversation_session(user, request.user.client_app, title=title)

        conversation_id = str(conversation.id)
        automation_metadata["conversation_id"] = conversation_id

    # Modify automation with updated query, subject
    automation.modify(
        name=json.dumps(automation_metadata),
        kwargs={
            "query_to_run": query_to_run,
            "subject": subject,
            "scheduling_request": q,
            "user": user,
            "calling_url": request.url,
            "conversation_id": conversation_id,
        },
    )

    # Reschedule automation if crontime updated
    user_timezone = pytz.timezone(timezone)
    trigger = CronTrigger.from_crontab(crontime, user_timezone)
    if automation.trigger != trigger:
        automation.reschedule(trigger=trigger)

    # Collate info about the updated user automation
    automation = AutomationAdapters.get_automation(user, automation.id)
    automation_info = AutomationAdapters.get_automation_metadata(user, automation)

    # Return modified automation information as a JSON response
    return Response(content=json.dumps(automation_info), media_type="application/json", status_code=200)
