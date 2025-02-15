import asyncio
import base64
import datetime
import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Any, Callable, List, NamedTuple, Optional

import aiohttp
from asgiref.sync import sync_to_async
from httpx import RemoteProtocolError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from khoj.database.adapters import FileObjectAdapters
from khoj.database.models import Agent, FileObject, KhojUser
from khoj.processor.conversation import prompts
from khoj.processor.conversation.utils import (
    ChatEvent,
    clean_code_python,
    construct_chat_history,
)
from khoj.routers.helpers import send_message_to_model_wrapper
from khoj.utils.helpers import (
    is_e2b_code_sandbox_enabled,
    is_none_or_empty,
    timer,
    truncate_code_context,
)
from khoj.utils.rawconfig import LocationData

logger = logging.getLogger(__name__)


SANDBOX_URL = os.getenv("KHOJ_TERRARIUM_URL", "http://localhost:8080")


class GeneratedCode(NamedTuple):
    code: str
    input_files: List[FileObject]


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
    for f in generated_code.input_files:
        input_data.append(
            {
                "filename": f.file_name,
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
    except asyncio.TimeoutError as e:
        # Call the sandbox_url/stop GET API endpoint to stop the code sandbox
        error = f"Failed to run code for {query} with Timeout error: {e}"
        try:
            await aiohttp.ClientSession().get(f"{sandbox_url}/stop", timeout=5)
        except Exception as e:
            error += f"\n\nFailed to stop code sandbox with error: {e}"
        raise ValueError(error)
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

    # add sandbox specific context like available packages
    sandbox_context = (
        prompts.e2b_sandbox_context if is_e2b_code_sandbox_enabled() else prompts.terrarium_sandbox_context
    )
    personality_context = f"{sandbox_context}\n{personality_context}"

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
        user=user,
        tracer=tracer,
        query_files=query_files,
    )

    # Extract python code wrapped in markdown code blocs from the response
    code_blocks = re.findall(r"```(?:python)?\n(.*?)\n```", response, re.DOTALL)

    if not code_blocks:
        raise ValueError("No Python code blocks found in response")

    # Join multiple code blocks with newlines and strip any leading/trailing whitespace
    code = "\n".join(code_blocks).strip()

    if not isinstance(code, str) or is_none_or_empty(code):
        raise ValueError

    # Infer user files required in sandbox based on user file paths mentioned in code
    input_files: List[FileObject] = []
    user_files = await sync_to_async(set)(FileObjectAdapters.get_all_file_objects(user))
    for user_file in user_files:
        if user_file.file_name in code:
            # Replace references to full file path used in code with just the file basename to ease reference in sandbox
            file_basename = os.path.basename(user_file.file_name)
            code = code.replace(user_file.file_name, file_basename)
            user_file.file_name = file_basename
            input_files.append(user_file)

    return GeneratedCode(code, input_files)


@retry(
    retry=(
        retry_if_exception_type(aiohttp.ClientError)
        | retry_if_exception_type(aiohttp.ClientTimeout)
        | retry_if_exception_type(asyncio.TimeoutError)
        | retry_if_exception_type(ConnectionError)
        | retry_if_exception_type(RemoteProtocolError)
    ),
    wait=wait_random_exponential(min=1, max=5),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.DEBUG),
    reraise=True,
)
async def execute_sandboxed_python(code: str, input_data: list[dict], sandbox_url: str = SANDBOX_URL) -> dict[str, Any]:
    """
    Takes code to run as a string and calls the terrarium API to execute it.
    Returns the result of the code execution as a dictionary.

    Reference data i/o format based on Terrarium example client code at:
    https://github.com/cohere-ai/cohere-terrarium/blob/main/example-clients/python/terrarium_client.py
    """
    cleaned_code = clean_code_python(code)
    if is_e2b_code_sandbox_enabled():
        try:
            return await execute_e2b(cleaned_code, input_data)
        except ImportError:
            pass
    return await execute_terrarium(cleaned_code, input_data, sandbox_url)


async def execute_e2b(code: str, input_files: list[dict]) -> dict[str, Any]:
    """Execute code and handle file I/O in e2b sandbox"""
    from e2b_code_interpreter import AsyncSandbox

    sandbox = await AsyncSandbox.create(
        api_key=os.getenv("E2B_API_KEY"),
        template=os.getenv("E2B_TEMPLATE", "pmt2o0ghpang8gbiys57"),
        timeout=120,
        request_timeout=30,
    )

    try:
        # Upload input files in parallel
        upload_tasks = [
            sandbox.files.write(path=file["filename"], data=base64.b64decode(file["b64_data"]), request_timeout=30)
            for file in input_files
        ]
        await asyncio.gather(*upload_tasks)

        # Note stored files before execution
        E2bFile = NamedTuple("E2bFile", [("name", str), ("path", str)])
        original_files = {E2bFile(f.name, f.path) for f in await sandbox.files.list("~")}

        # Execute code from main.py file
        execution = await sandbox.run_code(code=code, timeout=60)

        # Collect output files
        output_files = []

        # Identify new files created during execution
        new_files = set(E2bFile(f.name, f.path) for f in await sandbox.files.list("~")) - original_files
        # Read newly created files in parallel
        download_tasks = [sandbox.files.read(f.path, request_timeout=30) for f in new_files]
        downloaded_files = await asyncio.gather(*download_tasks)
        for f, content in zip(new_files, downloaded_files):
            if isinstance(content, bytes):
                # Binary files like PNG - encode as base64
                b64_data = base64.b64encode(content).decode("utf-8")
            else:
                # Text files - encode utf-8 string as base64
                b64_data = base64.b64encode(content.encode("utf-8")).decode("utf-8")
            output_files.append({"filename": f.name, "b64_data": b64_data})

        # Collect output files from execution results
        for idx, result in enumerate(execution.results):
            for result_type in ["png", "jpeg", "svg", "text", "markdown", "json"]:
                if b64_data := getattr(result, result_type, None):
                    output_files.append({"filename": f"{idx}.{result_type}", "b64_data": b64_data})
                    break

        # collect logs
        success = not execution.error and not execution.logs.stderr
        stdout = "\n".join(execution.logs.stdout)
        errors = "\n".join(execution.logs.stderr)
        if execution.error:
            errors = f"{execution.error}\n{errors}"

        return {
            "code": code,
            "success": success,
            "std_out": stdout,
            "std_err": errors,
            "output_files": output_files,
        }
    except Exception as e:
        return {
            "code": code,
            "success": False,
            "std_err": f"Sandbox failed to execute code: {str(e)}",
            "output_files": [],
        }


async def execute_terrarium(
    code: str,
    input_data: list[dict],
    sandbox_url: str,
) -> dict[str, Any]:
    """Execute code using Terrarium sandbox"""
    headers = {"Content-Type": "application/json"}
    data = {"code": code, "files": input_data}
    async with aiohttp.ClientSession() as session:
        async with session.post(sandbox_url, json=data, headers=headers, timeout=30) as response:
            if response.status == 200:
                result: dict[str, Any] = await response.json()
                result["code"] = code
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
                    "code": code,
                    "success": False,
                    "std_err": f"Failed to execute code with {response.status}",
                    "output_files": [],
                }
