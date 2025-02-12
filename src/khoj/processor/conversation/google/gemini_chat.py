import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pyjson5
from langchain.schema import ChatMessage

from khoj.database.models import Agent, ChatModel, KhojUser
from khoj.processor.conversation import prompts
from khoj.processor.conversation.google.utils import (
    format_messages_for_gemini,
    gemini_chat_completion_with_backoff,
    gemini_completion_with_backoff,
)
from khoj.processor.conversation.utils import (
    clean_json,
    construct_structured_message,
    generate_chatml_messages_with_context,
    messages_to_print,
)
from khoj.utils.helpers import (
    ConversationCommand,
    is_none_or_empty,
    truncate_code_context,
)
from khoj.utils.rawconfig import FileAttachment, LocationData
from khoj.utils.yaml import yaml_dump

logger = logging.getLogger(__name__)


def extract_questions_gemini(
    text,
    model: Optional[str] = "gemini-2.0-flash",
    conversation_log={},
    api_key=None,
    temperature=0,
    max_tokens=None,
    location_data: LocationData = None,
    user: KhojUser = None,
    query_images: Optional[list[str]] = None,
    vision_enabled: bool = False,
    personality_context: Optional[str] = None,
    query_files: str = None,
    tracer: dict = {},
):
    """
    Infer search queries to retrieve relevant notes to answer user query
    """
    # Extract Past User Message and Inferred Questions from Conversation Log
    location = f"{location_data}" if location_data else "Unknown"
    username = prompts.user_name.format(name=user.get_full_name()) if user and user.get_full_name() else ""

    # Extract Past User Message and Inferred Questions from Conversation Log
    chat_history = "".join(
        [
            f'User: {chat["intent"]["query"]}\nAssistant: {{"queries": {chat["intent"].get("inferred-queries") or list([chat["intent"]["query"]])}}}\nA: {chat["message"]}\n\n'
            for chat in conversation_log.get("chat", [])[-4:]
            if chat["by"] == "khoj"
        ]
    )

    # Get dates relative to today for prompt creation
    today = datetime.today()
    current_new_year = today.replace(month=1, day=1)
    last_new_year = current_new_year.replace(year=today.year - 1)

    system_prompt = prompts.extract_questions_anthropic_system_prompt.format(
        current_date=today.strftime("%Y-%m-%d"),
        day_of_week=today.strftime("%A"),
        current_month=today.strftime("%Y-%m"),
        last_new_year=last_new_year.strftime("%Y"),
        last_new_year_date=last_new_year.strftime("%Y-%m-%d"),
        current_new_year_date=current_new_year.strftime("%Y-%m-%d"),
        yesterday_date=(today - timedelta(days=1)).strftime("%Y-%m-%d"),
        location=location,
        username=username,
        personality_context=personality_context,
    )

    prompt = prompts.extract_questions_anthropic_user_message.format(
        chat_history=chat_history,
        text=text,
    )

    prompt = construct_structured_message(
        message=prompt,
        images=query_images,
        model_type=ChatModel.ModelType.GOOGLE,
        vision_enabled=vision_enabled,
        attached_file_context=query_files,
    )

    messages = []

    messages.append(ChatMessage(content=prompt, role="user"))
    messages.append(ChatMessage(content=system_prompt, role="system"))

    response = gemini_send_message_to_model(
        messages, api_key, model, response_type="json_object", temperature=temperature, tracer=tracer
    )

    # Extract, Clean Message from Gemini's Response
    try:
        response = clean_json(response)
        response = pyjson5.loads(response)
        response = [q.strip() for q in response["queries"] if q.strip()]
        if not isinstance(response, list) or not response:
            logger.error(f"Invalid response for constructing subqueries: {response}")
            return [text]
        return response
    except:
        logger.warning(f"Gemini returned invalid JSON. Falling back to using user message as search query.\n{response}")
        questions = [text]
    logger.debug(f"Extracted Questions by Gemini: {questions}")
    return questions


def gemini_send_message_to_model(
    messages,
    api_key,
    model,
    response_type="text",
    temperature=0,
    model_kwargs=None,
    tracer={},
):
    """
    Send message to model
    """
    messages, system_prompt = format_messages_for_gemini(messages)

    model_kwargs = {}

    # This caused unwanted behavior and terminates response early for gemini 1.5 series. Monitor for flakiness with 2.0 series.
    if response_type == "json_object" and model in ["gemini-2.0-flash"]:
        model_kwargs["response_mime_type"] = "application/json"

    # Get Response from Gemini
    return gemini_completion_with_backoff(
        messages=messages,
        system_prompt=system_prompt,
        model_name=model,
        api_key=api_key,
        temperature=temperature,
        model_kwargs=model_kwargs,
        tracer=tracer,
    )


def converse_gemini(
    references,
    user_query,
    online_results: Optional[Dict[str, Dict]] = None,
    code_results: Optional[Dict[str, Dict]] = None,
    conversation_log={},
    model: Optional[str] = "gemini-2.0-flash",
    api_key: Optional[str] = None,
    temperature: float = 0.2,
    completion_func=None,
    conversation_commands=[ConversationCommand.Default],
    max_prompt_size=None,
    tokenizer_name=None,
    location_data: LocationData = None,
    user_name: str = None,
    agent: Agent = None,
    query_images: Optional[list[str]] = None,
    vision_available: bool = False,
    query_files: str = None,
    generated_files: List[FileAttachment] = None,
    generated_asset_results: Dict[str, Dict] = {},
    program_execution_context: List[str] = None,
    tracer={},
):
    """
    Converse with user using Google's Gemini
    """
    # Initialize Variables
    current_date = datetime.now()

    if agent and agent.personality:
        system_prompt = prompts.custom_personality.format(
            name=agent.name,
            bio=agent.personality,
            current_date=current_date.strftime("%Y-%m-%d"),
            day_of_week=current_date.strftime("%A"),
        )
    else:
        system_prompt = prompts.personality.format(
            current_date=current_date.strftime("%Y-%m-%d"),
            day_of_week=current_date.strftime("%A"),
        )

    system_prompt += f"{system_prompt}\n\n{prompts.gemini_verbose_language_personality}"
    if location_data:
        location_prompt = prompts.user_location.format(location=f"{location_data}")
        system_prompt = f"{system_prompt}\n{location_prompt}"

    if user_name:
        user_name_prompt = prompts.user_name.format(name=user_name)
        system_prompt = f"{system_prompt}\n{user_name_prompt}"

    # Get Conversation Primer appropriate to Conversation Type
    if conversation_commands == [ConversationCommand.Notes] and is_none_or_empty(references):
        completion_func(chat_response=prompts.no_notes_found.format())
        return iter([prompts.no_notes_found.format()])
    elif conversation_commands == [ConversationCommand.Online] and is_none_or_empty(online_results):
        completion_func(chat_response=prompts.no_online_results_found.format())
        return iter([prompts.no_online_results_found.format()])

    context_message = ""
    if not is_none_or_empty(references):
        context_message = f"{prompts.notes_conversation.format(query=user_query, references=yaml_dump(references))}\n\n"
    if ConversationCommand.Online in conversation_commands or ConversationCommand.Webpage in conversation_commands:
        context_message += f"{prompts.online_search_conversation.format(online_results=yaml_dump(online_results))}\n\n"
    if ConversationCommand.Code in conversation_commands and not is_none_or_empty(code_results):
        context_message += (
            f"{prompts.code_executed_context.format(code_results=truncate_code_context(code_results))}\n\n"
        )
    context_message = context_message.strip()

    # Setup Prompt with Primer or Conversation History
    messages = generate_chatml_messages_with_context(
        user_query,
        context_message=context_message,
        conversation_log=conversation_log,
        model_name=model,
        max_prompt_size=max_prompt_size,
        tokenizer_name=tokenizer_name,
        query_images=query_images,
        vision_enabled=vision_available,
        model_type=ChatModel.ModelType.GOOGLE,
        query_files=query_files,
        generated_files=generated_files,
        generated_asset_results=generated_asset_results,
        program_execution_context=program_execution_context,
    )

    messages, system_prompt = format_messages_for_gemini(messages, system_prompt)
    logger.debug(f"Conversation Context for Gemini: {messages_to_print(messages)}")

    # Get Response from Google AI
    return gemini_chat_completion_with_backoff(
        messages=messages,
        compiled_references=references,
        online_results=online_results,
        model_name=model,
        temperature=temperature,
        api_key=api_key,
        system_prompt=system_prompt,
        completion_func=completion_func,
        tracer=tracer,
    )
