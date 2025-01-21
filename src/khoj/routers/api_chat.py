import asyncio
import base64
import json
import logging
import time
import uuid
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from asgiref.sync import sync_to_async
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.authentication import requires

from khoj.app.settings import ALLOWED_HOSTS
from khoj.database.adapters import (
    AgentAdapters,
    ConversationAdapters,
    EntryAdapters,
    PublicConversationAdapters,
    aget_user_name,
)
from khoj.database.models import Agent, KhojUser
from khoj.processor.conversation import prompts
from khoj.processor.conversation.prompts import help_message, no_entries_found
from khoj.processor.conversation.utils import defilter_query, save_to_conversation_log
from khoj.processor.image.generate import text_to_image
from khoj.processor.speech.text_to_speech import generate_text_to_speech
from khoj.processor.tools.online_search import (
    deduplicate_organic_results,
    read_webpages,
    search_online,
)
from khoj.processor.tools.run_code import run_code
from khoj.routers.api import extract_references_and_questions
from khoj.routers.email import send_query_feedback
from khoj.routers.helpers import (
    ApiImageRateLimiter,
    ApiUserRateLimiter,
    ChatEvent,
    ChatRequestBody,
    CommonQueryParams,
    ConversationCommandRateLimiter,
    DeleteMessageRequestBody,
    FeedbackData,
    acreate_title_from_history,
    agenerate_chat_response,
    aget_data_sources_and_output_format,
    construct_automation_created_message,
    create_automation,
    gather_raw_query_files,
    generate_mermaidjs_diagram,
    generate_summary_from_files,
    get_conversation_command,
    is_query_empty,
    is_ready_to_chat,
    read_chat_stream,
    update_telemetry_state,
    validate_chat_model,
)
from khoj.routers.research import (
    InformationCollectionIteration,
    execute_information_collection,
)
from khoj.routers.storage import upload_image_to_bucket
from khoj.utils import state
from khoj.utils.helpers import (
    AsyncIteratorWrapper,
    ConversationCommand,
    command_descriptions,
    convert_image_to_webp,
    get_country_code_from_timezone,
    get_country_name_from_timezone,
    get_device,
    is_none_or_empty,
)
from khoj.utils.rawconfig import (
    ChatRequestBody,
    FileAttachment,
    FileFilterRequest,
    FilesFilterRequest,
    LocationData,
)

# Initialize Router
logger = logging.getLogger(__name__)
conversation_command_rate_limiter = ConversationCommandRateLimiter(
    trial_rate_limit=20, subscribed_rate_limit=75, slug="command"
)


api_chat = APIRouter()


@api_chat.get("/conversation/file-filters/{conversation_id}", response_class=Response)
@requires(["authenticated"])
def get_file_filter(request: Request, conversation_id: str) -> Response:
    conversation = ConversationAdapters.get_conversation_by_user(request.user.object, conversation_id=conversation_id)
    if not conversation:
        return Response(content=json.dumps({"status": "error", "message": "Conversation not found"}), status_code=404)

    # get all files from "computer"
    file_list = EntryAdapters.get_all_filenames_by_source(request.user.object, "computer")
    file_filters = []
    for file in conversation.file_filters:
        if file in file_list:
            file_filters.append(file)
    return Response(content=json.dumps(file_filters), media_type="application/json", status_code=200)


@api_chat.delete("/conversation/file-filters/bulk", response_class=Response)
@requires(["authenticated"])
def remove_files_filter(request: Request, filter: FilesFilterRequest) -> Response:
    conversation_id = filter.conversation_id
    files_filter = filter.filenames
    file_filters = ConversationAdapters.remove_files_from_filter(request.user.object, conversation_id, files_filter)
    return Response(content=json.dumps(file_filters), media_type="application/json", status_code=200)


@api_chat.post("/conversation/file-filters/bulk", response_class=Response)
@requires(["authenticated"])
def add_files_filter(request: Request, filter: FilesFilterRequest):
    try:
        conversation_id = filter.conversation_id
        files_filter = filter.filenames
        file_filters = ConversationAdapters.add_files_to_filter(request.user.object, conversation_id, files_filter)
        return Response(content=json.dumps(file_filters), media_type="application/json", status_code=200)
    except Exception as e:
        logger.error(f"Error adding file filter {filter.filenames}: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=str(e))


@api_chat.post("/conversation/file-filters", response_class=Response)
@requires(["authenticated"])
def add_file_filter(request: Request, filter: FileFilterRequest):
    try:
        conversation_id = filter.conversation_id
        files_filter = [filter.filename]
        file_filters = ConversationAdapters.add_files_to_filter(request.user.object, conversation_id, files_filter)
        return Response(content=json.dumps(file_filters), media_type="application/json", status_code=200)
    except Exception as e:
        logger.error(f"Error adding file filter {filter.filename}: {e}", exc_info=True)
        raise HTTPException(status_code=422, detail=str(e))


@api_chat.delete("/conversation/file-filters", response_class=Response)
@requires(["authenticated"])
def remove_file_filter(request: Request, filter: FileFilterRequest) -> Response:
    conversation_id = filter.conversation_id
    files_filter = [filter.filename]
    file_filters = ConversationAdapters.remove_files_from_filter(request.user.object, conversation_id, files_filter)
    return Response(content=json.dumps(file_filters), media_type="application/json", status_code=200)


@api_chat.post("/feedback")
@requires(["authenticated"])
async def sendfeedback(request: Request, data: FeedbackData):
    user: KhojUser = request.user.object
    await send_query_feedback(data.uquery, data.kquery, data.sentiment, user.email)


@api_chat.post("/speech")
@requires(["authenticated"])
async def text_to_speech(
    request: Request,
    common: CommonQueryParams,
    text: str,
    rate_limiter_per_minute=Depends(
        ApiUserRateLimiter(requests=30, subscribed_requests=30, window=60, slug="chat_minute")
    ),
    rate_limiter_per_day=Depends(
        ApiUserRateLimiter(requests=100, subscribed_requests=600, window=60 * 60 * 24, slug="chat_day")
    ),
) -> Response:
    voice_model = await ConversationAdapters.aget_voice_model_config(request.user.object)

    params = {"text_to_speak": text}

    if voice_model:
        params["voice_id"] = voice_model.model_id

    speech_stream = generate_text_to_speech(**params)
    return StreamingResponse(speech_stream.iter_content(chunk_size=1024), media_type="audio/mpeg")


@api_chat.get("/starters", response_class=Response)
@requires(["authenticated"])
async def chat_starters(
    request: Request,
    common: CommonQueryParams,
) -> Response:
    user: KhojUser = request.user.object
    starter_questions = await ConversationAdapters.aget_conversation_starters(user)
    return Response(content=json.dumps(starter_questions), media_type="application/json", status_code=200)


@api_chat.get("/history")
@requires(["authenticated"])
def chat_history(
    request: Request,
    common: CommonQueryParams,
    conversation_id: Optional[str] = None,
    n: Optional[int] = None,
):
    user = request.user.object
    validate_chat_model(user)

    # Load Conversation History
    conversation = ConversationAdapters.get_conversation_by_user(
        user=user, client_application=request.user.client_app, conversation_id=conversation_id
    )

    if conversation is None:
        return Response(
            content=json.dumps({"status": "error", "message": f"Conversation: {conversation_id} not found"}),
            status_code=404,
        )

    agent_metadata = None
    if conversation.agent:
        if conversation.agent.privacy_level == Agent.PrivacyLevel.PRIVATE and conversation.agent.creator != user:
            conversation.agent = None
        else:
            agent_metadata = {
                "slug": conversation.agent.slug,
                "name": conversation.agent.name,
                "isCreator": conversation.agent.creator == user,
                "color": conversation.agent.style_color,
                "icon": conversation.agent.style_icon,
                "persona": conversation.agent.personality,
            }

    meta_log = conversation.conversation_log
    meta_log.update(
        {
            "conversation_id": conversation.id,
            "slug": conversation.title if conversation.title else conversation.slug,
            "agent": agent_metadata,
        }
    )

    if n:
        # Get latest N messages if N > 0
        if n > 0 and meta_log.get("chat"):
            meta_log["chat"] = meta_log["chat"][-n:]
        # Else return all messages except latest N
        elif n < 0 and meta_log.get("chat"):
            meta_log["chat"] = meta_log["chat"][:n]

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="chat_history",
        **common.__dict__,
    )

    return {"status": "ok", "response": meta_log}


@api_chat.get("/share/history")
def get_shared_chat(
    request: Request,
    common: CommonQueryParams,
    public_conversation_slug: str,
    n: Optional[int] = None,
):
    user = request.user.object if request.user.is_authenticated else None

    # Load Conversation History
    conversation = PublicConversationAdapters.get_public_conversation_by_slug(public_conversation_slug)

    if conversation is None:
        return Response(
            content=json.dumps({"status": "error", "message": f"Conversation: {public_conversation_slug} not found"}),
            status_code=404,
        )

    agent_metadata = None
    if conversation.agent:
        if conversation.agent.privacy_level == Agent.PrivacyLevel.PRIVATE:
            conversation.agent = None
        else:
            agent_metadata = {
                "slug": conversation.agent.slug,
                "name": conversation.agent.name,
                "isCreator": conversation.agent.creator == user,
                "color": conversation.agent.style_color,
                "icon": conversation.agent.style_icon,
                "persona": conversation.agent.personality,
            }

    meta_log = conversation.conversation_log
    scrubbed_title = conversation.title if conversation.title else conversation.slug

    if scrubbed_title:
        scrubbed_title = scrubbed_title.replace("-", " ")

    meta_log.update(
        {
            "conversation_id": conversation.id,
            "slug": scrubbed_title,
            "agent": agent_metadata,
        }
    )

    if n:
        # Get latest N messages if N > 0
        if n > 0 and meta_log.get("chat"):
            meta_log["chat"] = meta_log["chat"][-n:]
        # Else return all messages except latest N
        elif n < 0 and meta_log.get("chat"):
            meta_log["chat"] = meta_log["chat"][:n]

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="get_shared_chat_history",
        **common.__dict__,
    )

    return {"status": "ok", "response": meta_log}


@api_chat.delete("/history")
@requires(["authenticated"])
async def clear_chat_history(
    request: Request,
    common: CommonQueryParams,
    conversation_id: Optional[str] = None,
):
    user = request.user.object

    # Clear Conversation History
    await ConversationAdapters.adelete_conversation_by_user(user, request.user.client_app, conversation_id)

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="clear_chat_history",
        **common.__dict__,
    )

    return {"status": "ok", "message": "Conversation history cleared"}


@api_chat.post("/share/fork")
@requires(["authenticated"])
def fork_public_conversation(
    request: Request,
    common: CommonQueryParams,
    public_conversation_slug: str,
):
    user = request.user.object

    # Load Conversation History
    public_conversation = PublicConversationAdapters.get_public_conversation_by_slug(public_conversation_slug)

    # Duplicate Public Conversation to User's Private Conversation
    new_conversation = ConversationAdapters.create_conversation_from_public_conversation(
        user, public_conversation, request.user.client_app
    )

    chat_metadata = {"forked_conversation": public_conversation.slug}

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="fork_public_conversation",
        **common.__dict__,
        metadata=chat_metadata,
    )

    redirect_uri = str(request.app.url_path_for("chat_page"))

    return Response(
        status_code=200,
        content=json.dumps(
            {
                "status": "ok",
                "next_url": redirect_uri,
                "conversation_id": str(new_conversation.id),
            }
        ),
    )


@api_chat.post("/share")
@requires(["authenticated"])
def duplicate_chat_history_public_conversation(
    request: Request,
    common: CommonQueryParams,
    conversation_id: str,
):
    user = request.user.object
    domain = request.headers.get("host")
    scheme = request.url.scheme

    # Throw unauthorized exception if domain not in ALLOWED_HOSTS
    host_domain = domain.split(":")[0]
    if host_domain not in ALLOWED_HOSTS:
        raise HTTPException(status_code=401, detail="Unauthorized domain")

    # Duplicate Conversation History to Public Conversation
    conversation = ConversationAdapters.get_conversation_by_user(user, request.user.client_app, conversation_id)
    public_conversation = ConversationAdapters.make_public_conversation_copy(conversation)
    public_conversation_url = PublicConversationAdapters.get_public_conversation_url(public_conversation)

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="post_chat_share",
        **common.__dict__,
    )

    return Response(
        status_code=200, content=json.dumps({"status": "ok", "url": f"{scheme}://{domain}{public_conversation_url}"})
    )


@api_chat.get("/sessions")
@requires(["authenticated"])
def chat_sessions(
    request: Request,
    common: CommonQueryParams,
    recent: Optional[bool] = False,
):
    user = request.user.object

    # Load Conversation Sessions
    conversations = ConversationAdapters.get_conversation_sessions(user, request.user.client_app)
    if recent:
        conversations = conversations[:8]

    sessions = conversations.values_list(
        "id",
        "slug",
        "title",
        "agent__slug",
        "agent__name",
        "created_at",
        "updated_at",
        "agent__style_icon",
        "agent__style_color",
    )

    session_values = [
        {
            "conversation_id": str(session[0]),
            "slug": session[2] or session[1],
            "agent_name": session[4],
            "created": session[5].strftime("%Y-%m-%d %H:%M:%S"),
            "updated": session[6].strftime("%Y-%m-%d %H:%M:%S"),
            "agent_icon": session[7],
            "agent_color": session[8],
        }
        for session in sessions
    ]

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="chat_sessions",
        **common.__dict__,
    )

    return Response(content=json.dumps(session_values), media_type="application/json", status_code=200)


@api_chat.post("/sessions")
@requires(["authenticated"])
async def create_chat_session(
    request: Request,
    common: CommonQueryParams,
    agent_slug: Optional[str] = None,
):
    user = request.user.object

    # Create new Conversation Session
    conversation = await ConversationAdapters.acreate_conversation_session(user, request.user.client_app, agent_slug)

    response = {"conversation_id": str(conversation.id)}

    conversation_metadata = {
        "agent": agent_slug,
    }

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="create_chat_sessions",
        metadata=conversation_metadata,
        **common.__dict__,
    )

    return Response(content=json.dumps(response), media_type="application/json", status_code=200)


@api_chat.get("/options", response_class=Response)
async def chat_options(
    request: Request,
    common: CommonQueryParams,
) -> Response:
    cmd_options = {}
    for cmd in ConversationCommand:
        if cmd in command_descriptions:
            cmd_options[cmd.value] = command_descriptions[cmd]

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="chat_options",
        **common.__dict__,
    )
    return Response(content=json.dumps(cmd_options), media_type="application/json", status_code=200)


@api_chat.patch("/title", response_class=Response)
@requires(["authenticated"])
async def set_conversation_title(
    request: Request,
    common: CommonQueryParams,
    title: str,
    conversation_id: Optional[str] = None,
) -> Response:
    user = request.user.object
    title = title.strip()[:200]

    # Set Conversation Title
    conversation = await ConversationAdapters.aset_conversation_title(
        user, request.user.client_app, conversation_id, title
    )

    success = True if conversation else False

    update_telemetry_state(
        request=request,
        telemetry_type="api",
        api="set_conversation_title",
        **common.__dict__,
    )

    return Response(
        content=json.dumps({"status": "ok", "success": success}), media_type="application/json", status_code=200
    )


@api_chat.post("/title")
@requires(["authenticated"])
async def generate_chat_title(
    request: Request,
    common: CommonQueryParams,
    conversation_id: str,
):
    user: KhojUser = request.user.object
    conversation = await ConversationAdapters.aget_conversation_by_user(user=user, conversation_id=conversation_id)

    # Conversation.title is explicitly set by the user. Do not override.
    if conversation.title:
        return {"status": "ok", "title": conversation.title}

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    new_title = await acreate_title_from_history(request.user.object, conversation=conversation)
    conversation.slug = new_title[:200]

    await conversation.asave()

    return {"status": "ok", "title": new_title}


@api_chat.delete("/conversation/message", response_class=Response)
@requires(["authenticated"])
def delete_message(request: Request, delete_request: DeleteMessageRequestBody) -> Response:
    user = request.user.object
    success = ConversationAdapters.delete_message_by_turn_id(
        user, delete_request.conversation_id, delete_request.turn_id
    )
    if success:
        return Response(content=json.dumps({"status": "ok"}), media_type="application/json", status_code=200)
    else:
        return Response(content=json.dumps({"status": "error", "message": "Message not found"}), status_code=404)


@api_chat.post("")
@requires(["authenticated"])
async def chat(
    request: Request,
    common: CommonQueryParams,
    body: ChatRequestBody,
    rate_limiter_per_minute=Depends(
        ApiUserRateLimiter(requests=20, subscribed_requests=20, window=60, slug="chat_minute")
    ),
    rate_limiter_per_day=Depends(
        ApiUserRateLimiter(requests=100, subscribed_requests=600, window=60 * 60 * 24, slug="chat_day")
    ),
    image_rate_limiter=Depends(ApiImageRateLimiter(max_images=10, max_combined_size_mb=20)),
):
    # Access the parameters from the body
    q = body.q
    n = body.n
    d = body.d
    stream = body.stream
    title = body.title
    conversation_id = body.conversation_id
    turn_id = str(body.turn_id or uuid.uuid4())
    city = body.city
    region = body.region
    country = body.country or get_country_name_from_timezone(body.timezone)
    country_code = body.country_code or get_country_code_from_timezone(body.timezone)
    timezone = body.timezone
    raw_images = body.images
    raw_query_files = body.files

    async def event_generator(q: str, images: list[str]):
        start_time = time.perf_counter()
        ttft = None
        chat_metadata: dict = {}
        connection_alive = True
        user: KhojUser = request.user.object
        event_delimiter = "␃🔚␗"
        q = unquote(q)
        train_of_thought = []
        nonlocal conversation_id
        nonlocal raw_query_files

        tracer: dict = {
            "mid": turn_id,
            "cid": conversation_id,
            "uid": user.id,
            "khoj_version": state.khoj_version,
        }

        uploaded_images: list[str] = []
        if images:
            for image in images:
                decoded_string = unquote(image)
                base64_data = decoded_string.split(",", 1)[1]
                image_bytes = base64.b64decode(base64_data)
                webp_image_bytes = convert_image_to_webp(image_bytes)
                uploaded_image = upload_image_to_bucket(webp_image_bytes, request.user.object.id)
                if uploaded_image:
                    uploaded_images.append(uploaded_image)

        query_files: Dict[str, str] = {}
        if raw_query_files:
            for file in raw_query_files:
                query_files[file.name] = file.content

        async def send_event(event_type: ChatEvent, data: str | dict):
            nonlocal connection_alive, ttft, train_of_thought
            if not connection_alive or await request.is_disconnected():
                connection_alive = False
                logger.warning(f"User {user} disconnected from {common.client} client")
                return
            try:
                if event_type == ChatEvent.END_LLM_RESPONSE:
                    collect_telemetry()
                elif event_type == ChatEvent.START_LLM_RESPONSE:
                    ttft = time.perf_counter() - start_time
                elif event_type == ChatEvent.STATUS:
                    train_of_thought.append({"type": event_type.value, "data": data})

                if event_type == ChatEvent.MESSAGE:
                    yield data
                elif event_type == ChatEvent.REFERENCES or ChatEvent.METADATA or stream:
                    yield json.dumps({"type": event_type.value, "data": data}, ensure_ascii=False)
            except asyncio.CancelledError as e:
                connection_alive = False
                logger.warn(f"User {user} disconnected from {common.client} client: {e}")
                return
            except Exception as e:
                connection_alive = False
                logger.error(f"Failed to stream chat API response to {user} on {common.client}: {e}", exc_info=True)
                return
            finally:
                yield event_delimiter

        async def send_llm_response(response: str, usage: dict = None):
            # Send Chat Response
            async for result in send_event(ChatEvent.START_LLM_RESPONSE, ""):
                yield result
            async for result in send_event(ChatEvent.MESSAGE, response):
                yield result
            async for result in send_event(ChatEvent.END_LLM_RESPONSE, ""):
                yield result
            # Send Usage Metadata once llm interactions are complete
            if usage:
                async for event in send_event(ChatEvent.USAGE, usage):
                    yield event
            async for result in send_event(ChatEvent.END_RESPONSE, ""):
                yield result

        def collect_telemetry():
            # Gather chat response telemetry
            nonlocal chat_metadata
            latency = time.perf_counter() - start_time
            cmd_set = set([cmd.value for cmd in conversation_commands])
            cost = (tracer.get("usage", {}) or {}).get("cost", 0)
            chat_metadata = chat_metadata or {}
            chat_metadata["conversation_command"] = cmd_set
            chat_metadata["agent"] = conversation.agent.slug if conversation and conversation.agent else None
            chat_metadata["latency"] = f"{latency:.3f}"
            chat_metadata["ttft_latency"] = f"{ttft:.3f}"
            chat_metadata["cost"] = f"{cost:.5f}"

            logger.info(f"Chat response time to first token: {ttft:.3f} seconds")
            logger.info(f"Chat response total time: {latency:.3f} seconds")
            logger.info(f"Chat response cost: ${cost:.5f}")
            update_telemetry_state(
                request=request,
                telemetry_type="api",
                api="chat",
                client=common.client,
                user_agent=request.headers.get("user-agent"),
                host=request.headers.get("host"),
                metadata=chat_metadata,
            )

        if is_query_empty(q):
            async for result in send_llm_response("Please ask your query to get started.", tracer.get("usage")):
                yield result
            return

        # Automated tasks are handled before to allow mixing them with other conversation commands
        cmds_to_rate_limit = []
        is_automated_task = False
        if q.startswith("/automated_task"):
            is_automated_task = True
            q = q.replace("/automated_task", "").lstrip()
            cmds_to_rate_limit += [ConversationCommand.AutomatedTask]

        # Extract conversation command from query
        conversation_commands = [get_conversation_command(query=q)]

        conversation = await ConversationAdapters.aget_conversation_by_user(
            user,
            client_application=request.user.client_app,
            conversation_id=conversation_id,
            title=title,
            create_new=body.create_new,
        )
        if not conversation:
            async for result in send_llm_response(f"Conversation {conversation_id} not found", tracer.get("usage")):
                yield result
            return
        conversation_id = conversation.id

        async for event in send_event(ChatEvent.METADATA, {"conversationId": str(conversation_id), "turnId": turn_id}):
            yield event

        agent: Agent | None = None
        default_agent = await AgentAdapters.aget_default_agent()
        if conversation.agent and conversation.agent != default_agent:
            agent = conversation.agent

        if not conversation.agent:
            conversation.agent = default_agent
            await conversation.asave()
            agent = default_agent

        await is_ready_to_chat(user)
        user_name = await aget_user_name(user)
        location = None
        if city or region or country or country_code:
            location = LocationData(city=city, region=region, country=country, country_code=country_code)
        user_message_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        meta_log = conversation.conversation_log

        researched_results = ""
        online_results: Dict = dict()
        code_results: Dict = dict()
        generated_asset_results: Dict = dict()
        ## Extract Document References
        compiled_references: List[Any] = []
        inferred_queries: List[Any] = []
        file_filters = conversation.file_filters if conversation and conversation.file_filters else []
        attached_file_context = gather_raw_query_files(query_files)

        generated_images: List[str] = []
        generated_files: List[FileAttachment] = []
        generated_mermaidjs_diagram: str = None
        program_execution_context: List[str] = []

        if conversation_commands == [ConversationCommand.Default]:
            try:
                chosen_io = await aget_data_sources_and_output_format(
                    q,
                    meta_log,
                    is_automated_task,
                    user=user,
                    query_images=uploaded_images,
                    agent=agent,
                    query_files=attached_file_context,
                    tracer=tracer,
                )
            except ValueError as e:
                logger.error(f"Error getting data sources and output format: {e}. Falling back to default.")
                conversation_commands = [ConversationCommand.General]

            conversation_commands = chosen_io.get("sources") + [chosen_io.get("output")]

            # If we're doing research, we don't want to do anything else
            if ConversationCommand.Research in conversation_commands:
                conversation_commands = [ConversationCommand.Research]

            conversation_commands_str = ", ".join([cmd.value for cmd in conversation_commands])
            async for result in send_event(ChatEvent.STATUS, f"**Selected Tools:** {conversation_commands_str}"):
                yield result

        cmds_to_rate_limit += conversation_commands
        for cmd in cmds_to_rate_limit:
            try:
                await conversation_command_rate_limiter.update_and_check_if_valid(request, cmd)
                q = q.replace(f"/{cmd.value}", "").strip()
            except HTTPException as e:
                async for result in send_llm_response(str(e.detail), tracer.get("usage")):
                    yield result
                return

        defiltered_query = defilter_query(q)

        if conversation_commands == [ConversationCommand.Research]:
            async for research_result in execute_information_collection(
                request=request,
                user=user,
                query=defiltered_query,
                conversation_id=conversation_id,
                conversation_history=meta_log,
                query_images=uploaded_images,
                agent=agent,
                send_status_func=partial(send_event, ChatEvent.STATUS),
                user_name=user_name,
                location=location,
                file_filters=conversation.file_filters if conversation else [],
                query_files=attached_file_context,
                tracer=tracer,
            ):
                if isinstance(research_result, InformationCollectionIteration):
                    if research_result.summarizedResult:
                        if research_result.onlineContext:
                            online_results.update(research_result.onlineContext)
                        if research_result.codeContext:
                            code_results.update(research_result.codeContext)
                        if research_result.context:
                            compiled_references.extend(research_result.context)

                        researched_results += research_result.summarizedResult

                else:
                    yield research_result

            # researched_results = await extract_relevant_info(q, researched_results, agent)
            if state.verbose > 1:
                logger.debug(f"Researched Results: {researched_results}")

        used_slash_summarize = conversation_commands == [ConversationCommand.Summarize]
        file_filters = conversation.file_filters if conversation else []
        # Skip trying to summarize if
        if (
            # summarization intent was inferred
            ConversationCommand.Summarize in conversation_commands
            # and not triggered via slash command
            and not used_slash_summarize
            # but we can't actually summarize
            and len(file_filters) != 1
        ):
            conversation_commands.remove(ConversationCommand.Summarize)
        elif ConversationCommand.Summarize in conversation_commands:
            response_log = ""
            agent_has_entries = await EntryAdapters.aagent_has_entries(agent)
            if len(file_filters) == 0 and not agent_has_entries:
                response_log = "No files selected for summarization. Please add files using the section on the left."
                async for result in send_llm_response(response_log, tracer.get("usage")):
                    yield result
            else:
                async for response in generate_summary_from_files(
                    q=q,
                    user=user,
                    file_filters=file_filters,
                    meta_log=meta_log,
                    query_images=uploaded_images,
                    agent=agent,
                    send_status_func=partial(send_event, ChatEvent.STATUS),
                    query_files=attached_file_context,
                    tracer=tracer,
                ):
                    if isinstance(response, dict) and ChatEvent.STATUS in response:
                        yield response[ChatEvent.STATUS]
                    else:
                        if isinstance(response, str):
                            response_log = response
                            async for result in send_llm_response(response, tracer.get("usage")):
                                yield result

            summarized_document = FileAttachment(
                name="Summarized Document",
                content=response_log,
                type="text/plain",
                size=len(response_log.encode("utf-8")),
            )

            async for result in send_event(ChatEvent.GENERATED_ASSETS, {"files": [summarized_document.model_dump()]}):
                yield result

            generated_files.append(summarized_document)

        custom_filters = []
        if conversation_commands == [ConversationCommand.Help]:
            if not q:
                chat_model = await ConversationAdapters.aget_user_chat_model(user)
                if chat_model == None:
                    chat_model = await ConversationAdapters.aget_default_chat_model(user)
                model_type = chat_model.model_type
                formatted_help = help_message.format(model=model_type, version=state.khoj_version, device=get_device())
                async for result in send_llm_response(formatted_help, tracer.get("usage")):
                    yield result
                return
            # Adding specification to search online specifically on khoj.dev pages.
            custom_filters.append("site:khoj.dev")
            conversation_commands.append(ConversationCommand.Online)

        if ConversationCommand.Automation in conversation_commands:
            try:
                automation, crontime, query_to_run, subject = await create_automation(
                    q, timezone, user, request.url, meta_log, tracer=tracer
                )
            except Exception as e:
                logger.error(f"Error scheduling task {q} for {user.email}: {e}")
                error_message = f"Unable to create automation. Ensure the automation doesn't already exist."
                async for result in send_llm_response(error_message, tracer.get("usage")):
                    yield result
                return

            llm_response = construct_automation_created_message(automation, crontime, query_to_run, subject)
            await sync_to_async(save_to_conversation_log)(
                q,
                llm_response,
                user,
                meta_log,
                user_message_time,
                intent_type="automation",
                client_application=request.user.client_app,
                conversation_id=conversation_id,
                inferred_queries=[query_to_run],
                automation_id=automation.id,
                query_images=uploaded_images,
                train_of_thought=train_of_thought,
                raw_query_files=raw_query_files,
                tracer=tracer,
            )
            async for result in send_llm_response(llm_response, tracer.get("usage")):
                yield result
            return

        # Gather Context
        ## Extract Document References
        if not ConversationCommand.Research in conversation_commands:
            try:
                async for result in extract_references_and_questions(
                    request,
                    meta_log,
                    q,
                    (n or 7),
                    d,
                    conversation_id,
                    conversation_commands,
                    location,
                    partial(send_event, ChatEvent.STATUS),
                    query_images=uploaded_images,
                    agent=agent,
                    query_files=attached_file_context,
                    tracer=tracer,
                ):
                    if isinstance(result, dict) and ChatEvent.STATUS in result:
                        yield result[ChatEvent.STATUS]
                    else:
                        compiled_references.extend(result[0])
                        inferred_queries.extend(result[1])
                        defiltered_query = result[2]
            except Exception as e:
                error_message = (
                    f"Error searching knowledge base: {e}. Attempting to respond without document references."
                )
                logger.error(error_message, exc_info=True)
                async for result in send_event(
                    ChatEvent.STATUS, "Document search failed. I'll try respond without document references"
                ):
                    yield result

            if not is_none_or_empty(compiled_references):
                headings = "\n- " + "\n- ".join(set([c.get("compiled", c).split("\n")[0] for c in compiled_references]))
                # Strip only leading # from headings
                headings = headings.replace("#", "")
                async for result in send_event(ChatEvent.STATUS, f"**Found Relevant Notes**: {headings}"):
                    yield result

            if conversation_commands == [ConversationCommand.Notes] and not await EntryAdapters.auser_has_entries(user):
                async for result in send_llm_response(f"{no_entries_found.format()}", tracer.get("usage")):
                    yield result
                return

        if ConversationCommand.Notes in conversation_commands and is_none_or_empty(compiled_references):
            conversation_commands.remove(ConversationCommand.Notes)

        ## Gather Online References
        if ConversationCommand.Online in conversation_commands:
            try:
                async for result in search_online(
                    defiltered_query,
                    meta_log,
                    location,
                    user,
                    partial(send_event, ChatEvent.STATUS),
                    custom_filters,
                    query_images=uploaded_images,
                    agent=agent,
                    query_files=attached_file_context,
                    tracer=tracer,
                ):
                    if isinstance(result, dict) and ChatEvent.STATUS in result:
                        yield result[ChatEvent.STATUS]
                    else:
                        online_results = result
            except Exception as e:
                error_message = f"Error searching online: {e}. Attempting to respond without online results"
                logger.warning(error_message)
                async for result in send_event(
                    ChatEvent.STATUS, "Online search failed. I'll try respond without online references"
                ):
                    yield result

        ## Gather Webpage References
        if ConversationCommand.Webpage in conversation_commands:
            try:
                async for result in read_webpages(
                    defiltered_query,
                    meta_log,
                    location,
                    user,
                    partial(send_event, ChatEvent.STATUS),
                    query_images=uploaded_images,
                    agent=agent,
                    query_files=attached_file_context,
                    tracer=tracer,
                ):
                    if isinstance(result, dict) and ChatEvent.STATUS in result:
                        yield result[ChatEvent.STATUS]
                    else:
                        direct_web_pages = result
                webpages = []
                for query in direct_web_pages:
                    if online_results.get(query):
                        online_results[query]["webpages"] = direct_web_pages[query]["webpages"]
                    else:
                        online_results[query] = {"webpages": direct_web_pages[query]["webpages"]}

                    for webpage in direct_web_pages[query]["webpages"]:
                        webpages.append(webpage["link"])
                async for result in send_event(ChatEvent.STATUS, f"**Read web pages**: {webpages}"):
                    yield result
            except Exception as e:
                logger.warning(
                    f"Error reading webpages: {e}. Attempting to respond without webpage results",
                    exc_info=True,
                )
                async for result in send_event(
                    ChatEvent.STATUS, "Webpage read failed. I'll try respond without webpage references"
                ):
                    yield result

        ## Gather Code Results
        if ConversationCommand.Code in conversation_commands:
            try:
                context = f"# Iteration 1:\n#---\nNotes:\n{compiled_references}\n\nOnline Results:{online_results}"
                async for result in run_code(
                    defiltered_query,
                    meta_log,
                    context,
                    location,
                    user,
                    partial(send_event, ChatEvent.STATUS),
                    query_images=uploaded_images,
                    agent=agent,
                    query_files=attached_file_context,
                    tracer=tracer,
                ):
                    if isinstance(result, dict) and ChatEvent.STATUS in result:
                        yield result[ChatEvent.STATUS]
                    else:
                        code_results = result
                async for result in send_event(ChatEvent.STATUS, f"**Ran code snippets**: {len(code_results)}"):
                    yield result
            except ValueError as e:
                program_execution_context.append(f"Failed to run code")
                logger.warning(
                    f"Failed to use code tool: {e}. Attempting to respond without code results",
                    exc_info=True,
                )

        ## Send Gathered References
        unique_online_results = deduplicate_organic_results(online_results)
        async for result in send_event(
            ChatEvent.REFERENCES,
            {
                "inferredQueries": inferred_queries,
                "context": compiled_references,
                "onlineContext": unique_online_results,
                "codeContext": code_results,
            },
        ):
            yield result

        # Generate Output
        ## Generate Image Output
        if ConversationCommand.Image in conversation_commands:
            async for result in text_to_image(
                defiltered_query,
                user,
                meta_log,
                location_data=location,
                references=compiled_references,
                online_results=online_results,
                send_status_func=partial(send_event, ChatEvent.STATUS),
                query_images=uploaded_images,
                agent=agent,
                query_files=attached_file_context,
                tracer=tracer,
            ):
                if isinstance(result, dict) and ChatEvent.STATUS in result:
                    yield result[ChatEvent.STATUS]
                else:
                    generated_image, status_code, improved_image_prompt = result

            inferred_queries.append(improved_image_prompt)
            if generated_image is None or status_code != 200:
                program_execution_context.append(f"Failed to generate image with {improved_image_prompt}")
                async for result in send_event(ChatEvent.STATUS, f"Failed to generate image"):
                    yield result
            else:
                generated_images.append(generated_image)

                generated_asset_results["images"] = {
                    "query": improved_image_prompt,
                }

                async for result in send_event(
                    ChatEvent.GENERATED_ASSETS,
                    {
                        "images": [generated_image],
                    },
                ):
                    yield result

        if ConversationCommand.Diagram in conversation_commands:
            async for result in send_event(ChatEvent.STATUS, f"Creating diagram"):
                yield result

            inferred_queries = []
            diagram_description = ""

            async for result in generate_mermaidjs_diagram(
                q=defiltered_query,
                conversation_history=meta_log,
                location_data=location,
                note_references=compiled_references,
                online_results=online_results,
                query_images=uploaded_images,
                user=user,
                agent=agent,
                send_status_func=partial(send_event, ChatEvent.STATUS),
                query_files=attached_file_context,
                tracer=tracer,
            ):
                if isinstance(result, dict) and ChatEvent.STATUS in result:
                    yield result[ChatEvent.STATUS]
                else:
                    better_diagram_description_prompt, mermaidjs_diagram_description = result
                    if better_diagram_description_prompt and mermaidjs_diagram_description:
                        inferred_queries.append(better_diagram_description_prompt)
                        diagram_description = mermaidjs_diagram_description

                        generated_mermaidjs_diagram = diagram_description

                        generated_asset_results["diagrams"] = {
                            "query": better_diagram_description_prompt,
                        }

                        async for result in send_event(
                            ChatEvent.GENERATED_ASSETS,
                            {
                                "mermaidjsDiagram": mermaidjs_diagram_description,
                            },
                        ):
                            yield result
                    else:
                        error_message = "Failed to generate diagram. Please try again later."
                        program_execution_context.append(
                            prompts.failed_diagram_generation.format(
                                attempted_diagram=better_diagram_description_prompt
                            )
                        )

                        async for result in send_event(ChatEvent.STATUS, error_message):
                            yield result

        ## Generate Text Output
        async for result in send_event(ChatEvent.STATUS, f"**Generating a well-informed response**"):
            yield result

        llm_response, chat_metadata = await agenerate_chat_response(
            defiltered_query,
            meta_log,
            conversation,
            compiled_references,
            online_results,
            code_results,
            inferred_queries,
            conversation_commands,
            user,
            request.user.client_app,
            conversation_id,
            location,
            user_name,
            researched_results,
            uploaded_images,
            train_of_thought,
            attached_file_context,
            raw_query_files,
            generated_images,
            generated_files,
            generated_mermaidjs_diagram,
            program_execution_context,
            generated_asset_results,
            tracer,
        )

        # Send Response
        async for result in send_event(ChatEvent.START_LLM_RESPONSE, ""):
            yield result

        continue_stream = True
        iterator = AsyncIteratorWrapper(llm_response)
        async for item in iterator:
            if item is None:
                async for result in send_event(ChatEvent.END_LLM_RESPONSE, ""):
                    yield result
                # Send Usage Metadata once llm interactions are complete
                async for event in send_event(ChatEvent.USAGE, tracer.get("usage")):
                    yield event
                async for result in send_event(ChatEvent.END_RESPONSE, ""):
                    yield result
                logger.debug("Finished streaming response")
                return
            if not connection_alive or not continue_stream:
                continue
            try:
                async for result in send_event(ChatEvent.MESSAGE, f"{item}"):
                    yield result
            except Exception as e:
                continue_stream = False
                logger.info(f"User {user} disconnected. Emitting rest of responses to clear thread: {e}")

    ## Stream Text Response
    if stream:
        return StreamingResponse(event_generator(q, images=raw_images), media_type="text/plain")
    ## Non-Streaming Text Response
    else:
        response_iterator = event_generator(q, images=raw_images)
        response_data = await read_chat_stream(response_iterator)
        return Response(content=json.dumps(response_data), media_type="application/json", status_code=200)
