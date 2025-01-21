import base64
import json
import logging
import math
import mimetypes
import os
import queue
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from io import BytesIO
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional

import PIL.Image
import pyjson5
import requests
import tiktoken
import yaml
from langchain.schema import ChatMessage
from llama_cpp.llama import Llama
from transformers import AutoTokenizer

from khoj.database.adapters import ConversationAdapters
from khoj.database.models import ChatModel, ClientApplication, KhojUser
from khoj.processor.conversation import prompts
from khoj.processor.conversation.offline.utils import download_model, infer_max_tokens
from khoj.search_filter.base_filter import BaseFilter
from khoj.search_filter.date_filter import DateFilter
from khoj.search_filter.file_filter import FileFilter
from khoj.search_filter.word_filter import WordFilter
from khoj.utils import state
from khoj.utils.helpers import (
    ConversationCommand,
    is_none_or_empty,
    is_promptrace_enabled,
    merge_dicts,
)
from khoj.utils.rawconfig import FileAttachment
from khoj.utils.yaml import yaml_dump

logger = logging.getLogger(__name__)

try:
    from git import Repo
except ImportError:
    if is_promptrace_enabled():
        logger.warning("GitPython not installed. `pip install gitpython` to use prompt tracer.")

model_to_prompt_size = {
    # OpenAI Models
    "gpt-4o": 60000,
    "gpt-4o-mini": 60000,
    "o1": 20000,
    "o1-mini": 60000,
    # Google Models
    "gemini-1.5-flash": 60000,
    "gemini-1.5-pro": 60000,
    # Anthropic Models
    "claude-3-5-sonnet-20241022": 60000,
    "claude-3-5-haiku-20241022": 60000,
    # Offline Models
    "bartowski/Qwen2.5-14B-Instruct-GGUF": 20000,
    "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF": 20000,
    "bartowski/Llama-3.2-3B-Instruct-GGUF": 20000,
    "bartowski/gemma-2-9b-it-GGUF": 6000,
    "bartowski/gemma-2-2b-it-GGUF": 6000,
}
model_to_tokenizer: Dict[str, str] = {}


class ThreadedGenerator:
    def __init__(self, compiled_references, online_results, completion_func=None):
        self.queue = queue.Queue()
        self.compiled_references = compiled_references
        self.online_results = online_results
        self.completion_func = completion_func
        self.response = ""
        self.start_time = perf_counter()

    def __iter__(self):
        return self

    def __next__(self):
        item = self.queue.get()
        if item is StopIteration:
            time_to_response = perf_counter() - self.start_time
            logger.info(f"Chat streaming took: {time_to_response:.3f} seconds")
            if self.completion_func:
                # The completion func effectively acts as a callback.
                # It adds the aggregated response to the conversation history.
                self.completion_func(chat_response=self.response)
            raise StopIteration
        return item

    def send(self, data):
        if self.response == "":
            time_to_first_response = perf_counter() - self.start_time
            logger.info(f"First response took: {time_to_first_response:.3f} seconds")

        self.response += data
        self.queue.put(data)

    def close(self):
        self.queue.put(StopIteration)


class InformationCollectionIteration:
    def __init__(
        self,
        tool: str,
        query: str,
        context: list = None,
        onlineContext: dict = None,
        codeContext: dict = None,
        summarizedResult: str = None,
        warning: str = None,
    ):
        self.tool = tool
        self.query = query
        self.context = context
        self.onlineContext = onlineContext
        self.codeContext = codeContext
        self.summarizedResult = summarizedResult
        self.warning = warning


def construct_iteration_history(
    previous_iterations: List[InformationCollectionIteration], previous_iteration_prompt: str
) -> str:
    previous_iterations_history = ""
    for idx, iteration in enumerate(previous_iterations):
        iteration_data = previous_iteration_prompt.format(
            tool=iteration.tool,
            query=iteration.query,
            result=iteration.summarizedResult,
            index=idx + 1,
        )

        previous_iterations_history += iteration_data
    return previous_iterations_history


def construct_chat_history(conversation_history: dict, n: int = 4, agent_name="AI") -> str:
    chat_history = ""
    for chat in conversation_history.get("chat", [])[-n:]:
        if chat["by"] == "khoj" and chat["intent"].get("type") in ["remember", "reminder", "summarize"]:
            chat_history += f"User: {chat['intent']['query']}\n"

            if chat["intent"].get("inferred-queries"):
                chat_history += f'{agent_name}: {{"queries": {chat["intent"].get("inferred-queries")}}}\n'

            chat_history += f"{agent_name}: {chat['message']}\n\n"
        elif chat["by"] == "khoj" and chat.get("images"):
            chat_history += f"User: {chat['intent']['query']}\n"
            chat_history += f"{agent_name}: [generated image redacted for space]\n"
        elif chat["by"] == "khoj" and ("excalidraw" in chat["intent"].get("type")):
            chat_history += f"User: {chat['intent']['query']}\n"
            chat_history += f"{agent_name}: {chat['intent']['inferred-queries'][0]}\n"
        elif chat["by"] == "you":
            raw_query_files = chat.get("queryFiles")
            if raw_query_files:
                query_files: Dict[str, str] = {}
                for file in raw_query_files:
                    query_files[file["name"]] = file["content"]

                query_file_context = gather_raw_query_files(query_files)
                chat_history += f"User: {query_file_context}\n"

    return chat_history


def construct_tool_chat_history(
    previous_iterations: List[InformationCollectionIteration], tool: ConversationCommand = None
) -> Dict[str, list]:
    chat_history: list = []
    inferred_query_extractor: Callable[[InformationCollectionIteration], List[str]] = lambda x: []
    if tool == ConversationCommand.Notes:
        inferred_query_extractor = (
            lambda iteration: [c["query"] for c in iteration.context] if iteration.context else []
        )
    elif tool == ConversationCommand.Online:
        inferred_query_extractor = (
            lambda iteration: list(iteration.onlineContext.keys()) if iteration.onlineContext else []
        )
    elif tool == ConversationCommand.Code:
        inferred_query_extractor = lambda iteration: list(iteration.codeContext.keys()) if iteration.codeContext else []
    for iteration in previous_iterations:
        chat_history += [
            {
                "by": "you",
                "message": iteration.query,
            },
            {
                "by": "khoj",
                "intent": {
                    "type": "remember",
                    "inferred-queries": inferred_query_extractor(iteration),
                    "query": iteration.query,
                },
                "message": iteration.summarizedResult,
            },
        ]

    return {"chat": chat_history}


class ChatEvent(Enum):
    START_LLM_RESPONSE = "start_llm_response"
    END_LLM_RESPONSE = "end_llm_response"
    MESSAGE = "message"
    REFERENCES = "references"
    GENERATED_ASSETS = "generated_assets"
    STATUS = "status"
    METADATA = "metadata"
    USAGE = "usage"
    END_RESPONSE = "end_response"


def message_to_log(
    user_message,
    chat_response,
    user_message_metadata={},
    khoj_message_metadata={},
    conversation_log=[],
):
    """Create json logs from messages, metadata for conversation log"""
    default_khoj_message_metadata = {
        "intent": {"type": "remember", "memory-type": "notes", "query": user_message},
        "trigger-emotion": "calm",
    }
    khoj_response_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Filter out any fields that are set to None
    user_message_metadata = {k: v for k, v in user_message_metadata.items() if v is not None}
    khoj_message_metadata = {k: v for k, v in khoj_message_metadata.items() if v is not None}

    # Create json log from Human's message
    human_log = merge_dicts({"message": user_message, "by": "you"}, user_message_metadata)

    # Create json log from GPT's response
    khoj_log = merge_dicts(khoj_message_metadata, default_khoj_message_metadata)
    khoj_log = merge_dicts({"message": chat_response, "by": "khoj", "created": khoj_response_time}, khoj_log)

    conversation_log.extend([human_log, khoj_log])
    return conversation_log


def save_to_conversation_log(
    q: str,
    chat_response: str,
    user: KhojUser,
    meta_log: Dict,
    user_message_time: str = None,
    compiled_references: List[Dict[str, Any]] = [],
    online_results: Dict[str, Any] = {},
    code_results: Dict[str, Any] = {},
    inferred_queries: List[str] = [],
    intent_type: str = "remember",
    client_application: ClientApplication = None,
    conversation_id: str = None,
    automation_id: str = None,
    query_images: List[str] = None,
    raw_query_files: List[FileAttachment] = [],
    generated_images: List[str] = [],
    raw_generated_files: List[FileAttachment] = [],
    generated_mermaidjs_diagram: str = None,
    train_of_thought: List[Any] = [],
    tracer: Dict[str, Any] = {},
):
    user_message_time = user_message_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    turn_id = tracer.get("mid") or str(uuid.uuid4())

    user_message_metadata = {"created": user_message_time, "images": query_images, "turnId": turn_id}

    if raw_query_files and len(raw_query_files) > 0:
        user_message_metadata["queryFiles"] = [file.model_dump(mode="json") for file in raw_query_files]

    khoj_message_metadata = {
        "context": compiled_references,
        "intent": {"inferred-queries": inferred_queries, "type": intent_type},
        "onlineContext": online_results,
        "codeContext": code_results,
        "automationId": automation_id,
        "trainOfThought": train_of_thought,
        "turnId": turn_id,
        "images": generated_images,
        "queryFiles": [file.model_dump(mode="json") for file in raw_generated_files],
    }

    if generated_mermaidjs_diagram:
        khoj_message_metadata["mermaidjsDiagram"] = generated_mermaidjs_diagram

    updated_conversation = message_to_log(
        user_message=q,
        chat_response=chat_response,
        user_message_metadata=user_message_metadata,
        khoj_message_metadata=khoj_message_metadata,
        conversation_log=meta_log.get("chat", []),
    )
    ConversationAdapters.save_conversation(
        user,
        {"chat": updated_conversation},
        client_application=client_application,
        conversation_id=conversation_id,
        user_message=q,
    )

    if is_promptrace_enabled():
        merge_message_into_conversation_trace(q, chat_response, tracer)

    logger.info(
        f"""
Saved Conversation Turn
You ({user.username}): "{q}"

Khoj: "{chat_response}"
""".strip()
    )


def construct_structured_message(
    message: str, images: list[str], model_type: str, vision_enabled: bool, attached_file_context: str = None
):
    """
    Format messages into appropriate multimedia format for supported chat model types
    """
    if model_type in [
        ChatModel.ModelType.OPENAI,
        ChatModel.ModelType.GOOGLE,
        ChatModel.ModelType.ANTHROPIC,
    ]:
        if not attached_file_context and not (vision_enabled and images):
            return message

        constructed_messages: List[Any] = [{"type": "text", "text": message}]

        if not is_none_or_empty(attached_file_context):
            constructed_messages.append({"type": "text", "text": attached_file_context})
        if vision_enabled and images:
            for image in images:
                if image.startswith("https://"):
                    constructed_messages.append({"type": "image_url", "image_url": {"url": image}})
        return constructed_messages

    if not is_none_or_empty(attached_file_context):
        return f"{attached_file_context}\n\n{message}"

    return message


def gather_raw_query_files(
    query_files: Dict[str, str],
):
    """
    Gather contextual data from the given (raw) files
    """

    if len(query_files) == 0:
        return ""

    contextual_data = " ".join(
        [f"File: {file_name}\n\n{file_content}\n\n" for file_name, file_content in query_files.items()]
    )
    return f"I have attached the following files:\n\n{contextual_data}"


def generate_chatml_messages_with_context(
    user_message,
    system_message=None,
    conversation_log={},
    model_name="gpt-4o-mini",
    loaded_model: Optional[Llama] = None,
    max_prompt_size=None,
    tokenizer_name=None,
    query_images=None,
    vision_enabled=False,
    model_type="",
    context_message="",
    query_files: str = None,
    generated_files: List[FileAttachment] = None,
    generated_asset_results: Dict[str, Dict] = {},
    program_execution_context: List[str] = [],
):
    """Generate chat messages with appropriate context from previous conversation to send to the chat model"""
    # Set max prompt size from user config or based on pre-configured for model and machine specs
    if not max_prompt_size:
        if loaded_model:
            max_prompt_size = infer_max_tokens(loaded_model.n_ctx(), model_to_prompt_size.get(model_name, math.inf))
        else:
            max_prompt_size = model_to_prompt_size.get(model_name, 10000)

    # Scale lookback turns proportional to max prompt size supported by model
    lookback_turns = max_prompt_size // 750

    # Extract Chat History for Context
    chatml_messages: List[ChatMessage] = []
    for chat in conversation_log.get("chat", []):
        message_context = ""
        message_attached_files = ""

        generated_assets = {}

        chat_message = chat.get("message")
        role = "user" if chat["by"] == "you" else "assistant"

        # Legacy code to handle excalidraw diagrams prior to Dec 2024
        if chat["by"] == "khoj" and "excalidraw" in chat["intent"].get("type", ""):
            chat_message = chat["intent"].get("inferred-queries")[0]

        if not is_none_or_empty(chat.get("context")):
            references = "\n\n".join(
                {
                    f"# File: {item['file']}\n## {item['compiled']}\n"
                    for item in chat.get("context") or []
                    if isinstance(item, dict)
                }
            )
            message_context += f"{prompts.notes_conversation.format(references=references)}\n\n"

        if chat.get("queryFiles"):
            raw_query_files = chat.get("queryFiles")
            query_files_dict = dict()
            for file in raw_query_files:
                query_files_dict[file["name"]] = file["content"]

            message_attached_files = gather_raw_query_files(query_files_dict)
            chatml_messages.append(ChatMessage(content=message_attached_files, role=role))

        if not is_none_or_empty(chat.get("onlineContext")):
            message_context += f"{prompts.online_search_conversation.format(online_results=chat.get('onlineContext'))}"

        if not is_none_or_empty(message_context):
            reconstructed_context_message = ChatMessage(content=message_context, role="user")
            chatml_messages.insert(0, reconstructed_context_message)

        if not is_none_or_empty(chat.get("images")) and role == "assistant":
            generated_assets["image"] = {
                "query": chat.get("intent", {}).get("inferred-queries", [user_message])[0],
            }

        if not is_none_or_empty(chat.get("mermaidjsDiagram")) and role == "assistant":
            generated_assets["diagram"] = {
                "query": chat.get("intent", {}).get("inferred-queries", [user_message])[0],
            }

        if not is_none_or_empty(generated_assets):
            chatml_messages.append(
                ChatMessage(
                    content=f"{prompts.generated_assets_context.format(generated_assets=yaml_dump(generated_assets))}\n",
                    role="user",
                )
            )

        message_content = construct_structured_message(
            chat_message, chat.get("images") if role == "user" else [], model_type, vision_enabled
        )

        reconstructed_message = ChatMessage(content=message_content, role=role)
        chatml_messages.insert(0, reconstructed_message)

        if len(chatml_messages) >= 3 * lookback_turns:
            break

    messages = []

    if not is_none_or_empty(generated_asset_results):
        messages.append(
            ChatMessage(
                content=f"{prompts.generated_assets_context.format(generated_assets=yaml_dump(generated_asset_results))}\n\n",
                role="user",
            )
        )

    if not is_none_or_empty(user_message):
        messages.append(
            ChatMessage(
                content=construct_structured_message(
                    user_message, query_images, model_type, vision_enabled, query_files
                ),
                role="user",
            )
        )

    if generated_files:
        message_attached_files = gather_raw_query_files({file.name: file.content for file in generated_files})
        messages.append(ChatMessage(content=message_attached_files, role="assistant"))

    if program_execution_context:
        program_context_text = "\n".join(program_execution_context)
        context_message += f"{prompts.additional_program_context.format(context=program_context_text)}\n"

    if not is_none_or_empty(context_message):
        messages.append(ChatMessage(content=context_message, role="user"))

    if len(chatml_messages) > 0:
        messages += chatml_messages

    if not is_none_or_empty(system_message):
        messages.append(ChatMessage(content=system_message, role="system"))

    # Truncate oldest messages from conversation history until under max supported prompt size by model
    messages = truncate_messages(messages, max_prompt_size, model_name, loaded_model, tokenizer_name)

    # Return message in chronological order
    return messages[::-1]


def truncate_messages(
    messages: list[ChatMessage],
    max_prompt_size: int,
    model_name: str,
    loaded_model: Optional[Llama] = None,
    tokenizer_name=None,
) -> list[ChatMessage]:
    """Truncate messages to fit within max prompt size supported by model"""
    default_tokenizer = "gpt-4o"

    try:
        if loaded_model:
            encoder = loaded_model.tokenizer()
        elif model_name.startswith("gpt-") or model_name.startswith("o1"):
            # as tiktoken doesn't recognize o1 model series yet
            encoder = tiktoken.encoding_for_model("gpt-4o" if model_name.startswith("o1") else model_name)
        elif tokenizer_name:
            if tokenizer_name in state.pretrained_tokenizers:
                encoder = state.pretrained_tokenizers[tokenizer_name]
            else:
                encoder = AutoTokenizer.from_pretrained(tokenizer_name)
                state.pretrained_tokenizers[tokenizer_name] = encoder
        else:
            encoder = download_model(model_name).tokenizer()
    except:
        encoder = tiktoken.encoding_for_model(default_tokenizer)
        logger.debug(
            f"Fallback to default chat model tokenizer: {default_tokenizer}.\nConfigure tokenizer for model: {model_name} in Khoj settings to improve context stuffing."
        )

    # Extract system message from messages
    system_message = None
    for idx, message in enumerate(messages):
        if message.role == "system":
            system_message = messages.pop(idx)
            break

    # TODO: Handle truncation of multi-part message.content, i.e when message.content is a list[dict] rather than a string
    system_message_tokens = (
        len(encoder.encode(system_message.content)) if system_message and type(system_message.content) == str else 0
    )

    tokens = sum([len(encoder.encode(message.content)) for message in messages if type(message.content) == str])

    # Drop older messages until under max supported prompt size by model
    # Reserves 4 tokens to demarcate each message (e.g <|im_start|>user, <|im_end|>, <|endoftext|> etc.)
    while (tokens + system_message_tokens + 4 * len(messages)) > max_prompt_size and len(messages) > 1:
        messages.pop()
        tokens = sum([len(encoder.encode(message.content)) for message in messages if type(message.content) == str])

    # Truncate current message if still over max supported prompt size by model
    if (tokens + system_message_tokens) > max_prompt_size:
        current_message = "\n".join(messages[0].content.split("\n")[:-1]) if type(messages[0].content) == str else ""
        original_question = "\n".join(messages[0].content.split("\n")[-1:]) if type(messages[0].content) == str else ""
        original_question = f"\n{original_question}"
        original_question_tokens = len(encoder.encode(original_question))
        remaining_tokens = max_prompt_size - system_message_tokens
        if remaining_tokens > original_question_tokens:
            remaining_tokens -= original_question_tokens
            truncated_message = encoder.decode(encoder.encode(current_message)[:remaining_tokens]).strip()
            messages = [ChatMessage(content=truncated_message + original_question, role=messages[0].role)]
        else:
            truncated_message = encoder.decode(encoder.encode(original_question)[:remaining_tokens]).strip()
            messages = [ChatMessage(content=truncated_message, role=messages[0].role)]
        logger.debug(
            f"Truncate current message to fit within max prompt size of {max_prompt_size} supported by {model_name} model:\n {truncated_message[:1000]}..."
        )

    if system_message:
        # Default system message role is system.
        # Fallback to system message role of user for models that do not support this role like gemma-2 and openai's o1 model series.
        system_message.role = "user" if "gemma-2" in model_name or model_name.startswith("o1") else "system"
    return messages + [system_message] if system_message else messages


def reciprocal_conversation_to_chatml(message_pair):
    """Convert a single back and forth between user and assistant to chatml format"""
    return [ChatMessage(content=message, role=role) for message, role in zip(message_pair, ["user", "assistant"])]


def clean_json(response: str):
    """Remove any markdown json codeblock and newline formatting if present. Useful for non schema enforceable models"""
    return response.strip().replace("\n", "").removeprefix("```json").removesuffix("```")


def clean_mermaidjs(response: str):
    """Remove any markdown mermaidjs codeblock and newline formatting if present. Useful for non schema enforceable models"""
    return response.strip().removeprefix("```mermaid").removesuffix("```")


def clean_code_python(code: str):
    """Remove any markdown codeblock and newline formatting if present. Useful for non schema enforceable models"""
    return code.strip().removeprefix("```python").removesuffix("```")


def load_complex_json(json_str):
    """
    Preprocess a raw JSON string to escape unescaped double quotes within value strings,
    while preserving the JSON structure and already escaped quotes.
    """

    def replace_unescaped_quotes(match):
        # Get the content between colons and commas/end braces
        content = match.group(1)
        # Replace unescaped double, single quotes that aren't already escaped
        # Uses negative lookbehind to avoid replacing already escaped quotes
        # Replace " with \"
        processed_dq = re.sub(r'(?<!\\)"', '\\"', content)
        # Replace \' with \\'
        processed_final = re.sub(r"(?<!\\)\\'", r"\\\\'", processed_dq)
        return f': "{processed_final}"'

    # Match content between : and either , or }
    # This pattern looks for ': ' followed by any characters until , or }
    pattern = r':\s*"(.*?)(?<!\\)"(?=[,}])'

    # Process the JSON string
    cleaned = clean_json(rf"{json_str}")
    processed = re.sub(pattern, replace_unescaped_quotes, cleaned)

    # See which json loader can load the processed JSON as valid
    errors = []
    json_loaders_to_try = [json.loads, pyjson5.loads]
    for loads in json_loaders_to_try:
        try:
            return loads(processed)
        except (json.JSONDecodeError, pyjson5.Json5Exception) as e:
            errors.append(f"{type(e).__name__}: {str(e)}")

    # If all loaders fail, raise the aggregated error
    raise ValueError(
        f"Failed to load JSON with errors: {'; '.join(errors)}\n\n"
        f"While attempting to load this cleaned JSON:\n{processed}"
    )


def defilter_query(query: str):
    """Remove any query filters in query"""
    defiltered_query = query
    filters: List[BaseFilter] = [WordFilter(), FileFilter(), DateFilter()]
    for filter in filters:
        defiltered_query = filter.defilter(defiltered_query)
    return defiltered_query


@dataclass
class ImageWithType:
    content: Any
    type: str


def get_image_from_url(image_url: str, type="pil"):
    try:
        response = requests.get(image_url)
        response.raise_for_status()  # Check if the request was successful

        # Get content type from response or infer from URL
        content_type = response.headers.get("content-type") or mimetypes.guess_type(image_url)[0] or "image/webp"

        # Convert image to desired format
        if type == "b64":
            image_data = base64.b64encode(response.content).decode("utf-8")
        elif type == "pil":
            image_data = PIL.Image.open(BytesIO(response.content))
        else:
            raise ValueError(f"Invalid image type: {type}")

        return ImageWithType(content=image_data, type=content_type)
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get image from URL {image_url}: {e}")
        return ImageWithType(content=None, type=None)


def commit_conversation_trace(
    session: list[ChatMessage],
    response: str | list[dict],
    tracer: dict,
    system_message: str | list[dict] = "",
    repo_path: str = None,
) -> str:
    """
    Save trace of conversation step using git. Useful to visualize, compare and debug traces.
    Returns the path to the repository.
    """
    try:
        from git import Repo
    except ImportError:
        return None

    # Infer repository path from environment variable or provided path
    repo_path = repo_path if not is_none_or_empty(repo_path) else os.getenv("PROMPTRACE_DIR")
    if not repo_path:
        return None

    # Serialize session, system message and response to yaml
    system_message_yaml = json.dumps(system_message, ensure_ascii=False, sort_keys=False)
    response_yaml = json.dumps(response, ensure_ascii=False, sort_keys=False)
    formatted_session = [{"role": message.role, "content": message.content} for message in session]
    session_yaml = json.dumps(formatted_session, ensure_ascii=False, sort_keys=False)
    query = (
        json.dumps(session[-1].content, ensure_ascii=False, sort_keys=False).strip().removeprefix("'").removesuffix("'")
    )  # Extract serialized query from chat session

    # Extract chat metadata for session
    uid, cid, mid = tracer.get("uid", "main"), tracer.get("cid", "main"), tracer.get("mid")

    try:
        # Prepare git repository
        os.makedirs(repo_path, exist_ok=True)
        repo = Repo.init(repo_path)

        # Remove post-commit hook if it exists
        hooks_dir = os.path.join(repo_path, ".git", "hooks")
        post_commit_hook = os.path.join(hooks_dir, "post-commit")
        if os.path.exists(post_commit_hook):
            os.remove(post_commit_hook)

        # Configure git user if not set
        if not repo.config_reader().has_option("user", "email"):
            repo.config_writer().set_value("user", "name", "Prompt Tracer").release()
            repo.config_writer().set_value("user", "email", "promptracer@khoj.dev").release()

        # Create an initial commit if the repository is newly created
        if not repo.head.is_valid():
            repo.index.commit("And then there was a trace")

        # Check out the initial commit
        initial_commit = repo.commit("HEAD~0")
        repo.head.reference = initial_commit
        repo.head.reset(index=True, working_tree=True)

        # Create or switch to user branch from initial commit
        user_branch = f"u_{uid}"
        if user_branch not in repo.branches:
            repo.create_head(user_branch)
        repo.heads[user_branch].checkout()

        # Create or switch to conversation branch from user branch
        conv_branch = f"c_{cid}"
        if conv_branch not in repo.branches:
            repo.create_head(conv_branch)
        repo.heads[conv_branch].checkout()

        # Create or switch to message branch from conversation branch
        msg_branch = f"m_{mid}" if mid else None
        if msg_branch and msg_branch not in repo.branches:
            repo.create_head(msg_branch)
        if msg_branch:
            repo.heads[msg_branch].checkout()

        # Include file with content to commit
        files_to_commit = {"query": session_yaml, "response": response_yaml, "system_prompt": system_message_yaml}

        # Write files and stage them
        for filename, content in files_to_commit.items():
            file_path = os.path.join(repo_path, filename)
            # Unescape special characters in content for better readability
            content = content.strip().replace("\\n", "\n").replace("\\t", "\t")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            repo.index.add([filename])

        # Create commit
        metadata_yaml = yaml.dump(tracer, allow_unicode=True, sort_keys=False, default_flow_style=False)
        commit_message = f"""
{query[:250]}

Response:
---
{response[:500]}...

Metadata
---
{metadata_yaml}
""".strip()

        repo.index.commit(commit_message)

        logger.debug(f"Saved conversation trace to repo at {repo_path}")
        return repo_path
    except Exception as e:
        logger.error(f"Failed to add conversation trace to repo: {str(e)}", exc_info=True)
        return None


def merge_message_into_conversation_trace(query: str, response: str, tracer: dict, repo_path=None) -> bool:
    """
    Merge the message branch into its parent conversation branch.

    Args:
        query: User query
        response: Assistant response
        tracer: Dictionary containing uid, cid and mid
        repo_path: Path to the git repository

    Returns:
        bool: True if merge was successful, False otherwise
    """
    try:
        from git import Repo
    except ImportError:
        return False
    try:
        # Extract branch names
        msg_branch = f"m_{tracer['mid']}"
        conv_branch = f"c_{tracer['cid']}"

        # Infer repository path from environment variable or provided path
        repo_path = repo_path if not is_none_or_empty(repo_path) else os.getenv("PROMPTRACE_DIR")
        if not repo_path:
            return None
        repo = Repo(repo_path)

        # Checkout conversation branch
        repo.heads[conv_branch].checkout()

        # Create commit message
        metadata_yaml = yaml.dump(tracer, allow_unicode=True, sort_keys=False, default_flow_style=False)
        commit_message = f"""
{query[:250]}

Response:
---
{response[:500]}...

Metadata
---
{metadata_yaml}
""".strip()

        # Merge message branch into conversation branch
        repo.git.merge(msg_branch, no_ff=True, m=commit_message)

        # Delete message branch after merge
        repo.delete_head(msg_branch, force=True)

        logger.debug(f"Successfully merged {msg_branch} into {conv_branch}")
        return True
    except Exception as e:
        logger.error(f"Failed to merge message {msg_branch} into conversation {conv_branch}: {str(e)}", exc_info=True)
        return False


def messages_to_print(messages: list[ChatMessage], max_length: int = 70) -> str:
    """
    Format and truncate messages to print, ensuring JSON serializable content
    """

    def safe_serialize(content: Any) -> str:
        try:
            # Try JSON serialization
            json.dumps(content)
            return content
        except (TypeError, json.JSONDecodeError):
            # Handle non-serializable types
            if hasattr(content, "format") and content.format == "WEBP":
                return "[WebP Image]"
            elif hasattr(content, "__dict__"):
                return str(content.__dict__)
            return str(content)

    return "\n".join([f"{json.dumps(safe_serialize(message.content))[:max_length]}..." for message in messages])
