import asyncio
import base64
import datetime
import logging
import mimetypes
import os
from functools import wraps
from pathlib import Path
from typing import Any, Callable, List, NamedTuple, Optional

import aiohttp

from khoj.database.adapters import FileObjectAdapters
from khoj.database.models import Agent, FileObject, KhojUser
from khoj.processor.conversation import prompts
from khoj.processor.conversation.utils import (
    ChatEvent,
    clean_code_python,
    construct_chat_history,
    load_complex_json,
)
from khoj.routers.helpers import send_message_to_model_wrapper
from khoj.utils.helpers import is_none_or_empty, timer, truncate_code_context
from khoj.utils.rawconfig import LocationData

logger = logging.getLogger(__name__)


SANDBOX_URL = os.getenv("KHOJ_TERRARIUM_URL", "http://localhost:8080")


class GeneratedCode(NamedTuple):
    code: str
    input_files: List[str]
    input_links: List[str]


async def run_code(
    query: str,
    conversation_history: dict,
    context: str,
    location_data: LocationData,
    user: KhojUser,
    send_status_func: Optional[Callable] = None,
    query_images: List[str] = None,
    agent: Agent = None,
    sandbox_url: str = SANDBOX_URL,
    query_files: str = None,
    tracer: dict = {},
):
    # Generate Code
    if send_status_func:
        async for event in send_status_func(f"**Generate code snippet** for {query}"):
            yield {ChatEvent.STATUS: event}
    try:
        with timer("Chat actor: Generate programs to execute", logger):
            generated_code = await generate_python_code(
                query,
                conversation_history,
                context,
                location_data,
                user,
                query_images,
                agent,
                tracer,
                query_files,
            )
    except Exception as e:
        raise ValueError(f"Failed to generate code for {query} with error: {e}")

    # Prepare Input Data
    input_data = []
    user_input_files: List[FileObject] = []
    for input_file in generated_code.input_files:
        user_input_files += await FileObjectAdapters.aget_file_objects_by_name(user, input_file)
    for f in user_input_files:
        input_data.append(
            {
                "filename": os.path.basename(f.file_name),
                "b64_data": base64.b64encode(f.raw_text.encode("utf-8")).decode("utf-8"),
            }
        )

    # Run Code
    if send_status_func:
        async for event in send_status_func(f"**Running code snippet**"):
            yield {ChatEvent.STATUS: event}
    try:
        with timer("Chat actor: Execute generated program", logger, log_level=logging.INFO):
            result = await execute_sandboxed_python(generated_code.code, input_data, sandbox_url)
            code = result.pop("code")
            cleaned_result = truncate_code_context({"cleaned": {"results": result}})["cleaned"]["results"]
            logger.info(f"Executed Code\n----\n{code}\n----\nResult\n----\n{cleaned_result}\n----")
            yield {query: {"code": code, "results": result}}
    except Exception as e:
        raise ValueError(f"Failed to run code for {query} with error: {e}")


async def generate_python_code(
    q: str,
    conversation_history: dict,
    context: str,
    location_data: LocationData,
    user: KhojUser,
    query_images: list[str] = None,
    agent: Agent = None,
    tracer: dict = {},
    query_files: str = None,
) -> GeneratedCode:
    location = f"{location_data}" if location_data else "Unknown"
    username = prompts.user_name.format(name=user.get_full_name()) if user.get_full_name() else ""
    chat_history = construct_chat_history(conversation_history)

    utc_date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    personality_context = (
        prompts.personality_context.format(personality=agent.personality) if agent and agent.personality else ""
    )

    code_generation_prompt = prompts.python_code_generation_prompt.format(
        current_date=utc_date,
        query=q,
        chat_history=chat_history,
        context=context,
        location=location,
        username=username,
        personality_context=personality_context,
    )

    response = await send_message_to_model_wrapper(
        code_generation_prompt,
        query_images=query_images,
        response_type="json_object",
        user=user,
        tracer=tracer,
        query_files=query_files,
    )

    # Validate that the response is a non-empty, JSON-serializable list
    response = load_complex_json(response)
    code = response.get("code", "").strip()
    input_files = response.get("input_files", [])
    input_links = response.get("input_links", [])

    if not isinstance(code, str) or is_none_or_empty(code):
        raise ValueError
    return GeneratedCode(code, input_files, input_links)


def async_retry_with_backoff(retries=3, backoff_in_seconds=1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retry_count = 0
            while retry_count < retries:
                try:
                    return await func(*args, **kwargs)
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    retry_count += 1
                    if retry_count == retries:
                        raise e
                    wait_time = backoff_in_seconds * (2 ** (retry_count - 1))  # exponential backoff
                    await asyncio.sleep(wait_time)
            return await func(*args, **kwargs)

        return wrapper

    return decorator


@async_retry_with_backoff(retries=3, backoff_in_seconds=1)
async def execute_sandboxed_python(code: str, input_data: list[dict], sandbox_url: str = SANDBOX_URL) -> dict[str, Any]:
    """
    Takes code to run as a string and calls the terrarium API to execute it.
    Returns the result of the code execution as a dictionary.

    Reference data i/o format based on Terrarium example client code at:
    https://github.com/cohere-ai/cohere-terrarium/blob/main/example-clients/python/terrarium_client.py
    """
    headers = {"Content-Type": "application/json"}
    cleaned_code = clean_code_python(code)
    data = {"code": cleaned_code, "files": input_data}

    async with aiohttp.ClientSession() as session:
        async with session.post(sandbox_url, json=data, headers=headers, timeout=30) as response:
            if response.status == 200:
                result: dict[str, Any] = await response.json()
                result["code"] = cleaned_code
                # Store decoded output files
                result["output_files"] = result.get("output_files", [])
                for output_file in result["output_files"]:
                    # Decode text files as UTF-8
                    if mimetypes.guess_type(output_file["filename"])[0].startswith("text/") or Path(
                        output_file["filename"]
                    ).suffix in [".org", ".md", ".json"]:
                        output_file["b64_data"] = base64.b64decode(output_file["b64_data"]).decode("utf-8")
                return result
            else:
                return {
                    "code": cleaned_code,
                    "success": False,
                    "std_err": f"Failed to execute code with {response.status}",
                    "output_files": [],
                }
