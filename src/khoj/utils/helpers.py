from __future__ import annotations  # to avoid quoting type hints

import copy
import datetime
import io
import ipaddress
import logging
import os
import platform
import random
import urllib.parse
import uuid
from collections import OrderedDict
from enum import Enum
from functools import lru_cache
from importlib import import_module
from importlib.metadata import version
from itertools import islice
from os import path
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, Optional, Union
from urllib.parse import urlparse

import openai
import psutil
import requests
import torch
from asgiref.sync import sync_to_async
from email_validator import EmailNotValidError, EmailUndeliverableError, validate_email
from magika import Magika
from PIL import Image
from pytz import country_names, country_timezones

from khoj.utils import constants

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer

    from khoj.utils.models import BaseEncoder
    from khoj.utils.rawconfig import AppConfig


# Initialize Magika for file type identification
magika = Magika()


class AsyncIteratorWrapper:
    def __init__(self, obj):
        self._it = iter(obj)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            value = await self.next_async()
        except StopAsyncIteration:
            return
        return value

    @sync_to_async
    def next_async(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def is_none_or_empty(item):
    return item == None or (hasattr(item, "__iter__") and len(item) == 0) or item == ""


def to_snake_case_from_dash(item: str):
    return item.replace("_", "-")


def get_absolute_path(filepath: Union[str, Path]) -> str:
    return str(Path(filepath).expanduser().absolute())


def resolve_absolute_path(filepath: Union[str, Optional[Path]], strict=False) -> Path:
    return Path(filepath).expanduser().absolute().resolve(strict=strict)


def get_from_dict(dictionary, *args):
    """null-aware get from a nested dictionary
    Returns: dictionary[args[0]][args[1]]... or None if any keys missing"""
    current = dictionary
    for arg in args:
        if not hasattr(current, "__iter__") or not arg in current:
            return None
        current = current[arg]
    return current


def merge_dicts(priority_dict: dict, default_dict: dict):
    merged_dict = priority_dict.copy()
    for key, _ in default_dict.items():
        if key not in priority_dict:
            merged_dict[key] = default_dict[key]
        elif isinstance(priority_dict[key], dict) and isinstance(default_dict[key], dict):
            merged_dict[key] = merge_dicts(priority_dict[key], default_dict[key])
    return merged_dict


def fix_json_dict(json_dict: dict) -> dict:
    for k, v in json_dict.items():
        if v == "True" or v == "False":
            json_dict[k] = v == "True"
        if isinstance(v, dict):
            json_dict[k] = fix_json_dict(v)
    return json_dict


def get_file_type(file_type: str, file_content: bytes) -> tuple[str, str]:
    "Get file type from file mime type"

    # Extract encoding from file_type
    encoding = file_type.split("=")[1].strip().lower() if ";" in file_type else None
    file_type = file_type.split(";")[0].strip() if ";" in file_type else file_type

    # Infer content type from reading file content
    try:
        content_group = magika.identify_bytes(file_content).output.group
    except Exception:
        # Fallback to using just file type if content type cannot be inferred
        content_group = "unknown"

    if file_type in ["text/markdown"]:
        return "markdown", encoding
    elif file_type in ["text/org"]:
        return "org", encoding
    elif file_type in ["application/pdf"]:
        return "pdf", encoding
    elif file_type in ["application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]:
        return "docx", encoding
    elif file_type in ["image/jpeg"]:
        return "image", encoding
    elif file_type in ["image/png"]:
        return "image", encoding
    elif file_type in ["image/webp"]:
        return "image", encoding
    elif content_group in ["code", "text"]:
        return "plaintext", encoding
    else:
        return "other", encoding


def load_model(
    model_name: str, model_type, model_dir=None, device: str = None
) -> Union[BaseEncoder, SentenceTransformer, CrossEncoder]:
    "Load model from disk or huggingface"
    # Construct model path
    logger = logging.getLogger(__name__)
    model_path = path.join(model_dir, model_name.replace("/", "_")) if model_dir is not None else None

    # Load model from model_path if it exists there
    model_type_class = get_class_by_name(model_type) if isinstance(model_type, str) else model_type
    if model_path is not None and resolve_absolute_path(model_path).exists():
        logger.debug(f"Loading {model_name} model from disk")
        model = model_type_class(get_absolute_path(model_path), device=device)
    # Else load the model from the model_name
    else:
        logger.info(f"🤖 Downloading {model_name} model from web")
        model = model_type_class(model_name, device=device)
        if model_path is not None:
            logger.info(f"📩 Saved {model_name} model to disk")
            model.save(model_path)

    return model


def get_class_by_name(name: str) -> object:
    "Returns the class object from name string"
    module_name, class_name = name.rsplit(".", 1)
    return getattr(import_module(module_name), class_name)


class timer:
    """Context manager to log time taken for a block of code to run"""

    def __init__(self, message: str, logger: logging.Logger, device: torch.device = None, log_level=logging.DEBUG):
        self.message = message
        self.logger = logger.debug if log_level == logging.DEBUG else logger.info
        self.device = device

    def __enter__(self):
        self.start = perf_counter()
        return self

    def __exit__(self, *_):
        elapsed = perf_counter() - self.start
        if self.device is None:
            self.logger(f"{self.message}: {elapsed:.3f} seconds")
        else:
            self.logger(f"{self.message}: {elapsed:.3f} seconds on device: {self.device}")


class LRU(OrderedDict):
    def __init__(self, *args, capacity=128, **kwargs):
        self.capacity = capacity
        super().__init__(*args, **kwargs)

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if len(self) > self.capacity:
            oldest = next(iter(self))
            del self[oldest]


def get_server_id():
    """Get, Generate Persistent, Random ID per server install.
    Helps count distinct khoj servers deployed.
    Maintains anonymity by using non-PII random id."""
    # Initialize server_id to None
    server_id = None
    # Expand path to the khoj env file. It contains persistent internal app data
    app_env_filename = path.expanduser(constants.app_env_filepath)

    # Check if the file exists
    if path.exists(app_env_filename):
        # Read the contents of the file
        with open(app_env_filename, "r") as f:
            contents = f.readlines()

        # Extract the server_id from the contents
        for line in contents:
            key, value = line.strip().split("=")
            if key.strip() == "server_id":
                server_id = value.strip()
                break

        # If server_id is not found, generate and write to env file
        if server_id is None:
            # If server_id is not found, generate a new one
            server_id = str(uuid.uuid4())

            with open(app_env_filename, "a") as f:
                f.write("server_id=" + server_id + "\n")
    else:
        # If server_id is not found, generate a new one
        server_id = str(uuid.uuid4())

        # Create khoj config directory if it doesn't exist
        os.makedirs(path.dirname(app_env_filename), exist_ok=True)

        # Write the server_id to the env file
        with open(app_env_filename, "w") as f:
            f.write("server_id=" + server_id + "\n")

    return server_id


def telemetry_disabled(app_config: AppConfig, telemetry_disable_env) -> bool:
    if telemetry_disable_env is True:
        return True
    return not app_config or not app_config.should_log_telemetry


def log_telemetry(
    telemetry_type: str,
    api: str = None,
    client: Optional[str] = None,
    app_config: Optional[AppConfig] = None,
    disable_telemetry_env: bool = False,
    properties: dict = None,
):
    """Log basic app usage telemetry like client, os, api called"""
    # Do not log usage telemetry, if telemetry is disabled via app config
    if telemetry_disabled(app_config, disable_telemetry_env):
        return []

    if properties.get("server_id") is None:
        properties["server_id"] = get_server_id()

    # Populate telemetry data to log
    request_body = {
        "telemetry_type": telemetry_type,
        "server_version": version("khoj"),
        "os": platform.system(),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    request_body.update(properties or {})
    if api:
        # API endpoint on server called by client
        request_body["api"] = api
    if client:
        # Client from which the API was called. E.g. Emacs, Obsidian
        request_body["client"] = client

    # Log telemetry data to telemetry endpoint
    return request_body


def get_device_memory() -> int:
    """Get device memory in GB"""
    device = get_device()
    if device.type == "cuda":
        return torch.cuda.get_device_properties(device).total_memory
    elif device.type == "mps":
        return torch.mps.driver_allocated_memory()
    else:
        return psutil.virtual_memory().total


def get_device() -> torch.device:
    """Get device to run model on"""
    if torch.cuda.is_available():
        # Use CUDA GPU
        return torch.device("cuda:0")
    elif torch.backends.mps.is_available():
        # Use Apple M1 Metal Acceleration
        return torch.device("mps")
    else:
        return torch.device("cpu")


class ConversationCommand(str, Enum):
    Default = "default"
    General = "general"
    Notes = "notes"
    Help = "help"
    Online = "online"
    Webpage = "webpage"
    Code = "code"
    Image = "image"
    Text = "text"
    Automation = "automation"
    AutomatedTask = "automated_task"
    Summarize = "summarize"
    Diagram = "diagram"
    Research = "research"


command_descriptions = {
    ConversationCommand.General: "Only talk about information that relies on Khoj's general knowledge, not your personal knowledge base.",
    ConversationCommand.Notes: "Only talk about information that is available in your knowledge base.",
    ConversationCommand.Default: "The default command when no command specified. It intelligently auto-switches between general and notes mode.",
    ConversationCommand.Online: "Search for information on the internet.",
    ConversationCommand.Webpage: "Get information from webpage suggested by you.",
    ConversationCommand.Code: "Run Python code to parse information, run complex calculations, create documents and charts.",
    ConversationCommand.Image: "Generate illustrative, creative images by describing your imagination in words.",
    ConversationCommand.Automation: "Automatically run your query at a specified time or interval.",
    ConversationCommand.Help: "Get help with how to use or setup Khoj from the documentation",
    ConversationCommand.Summarize: "Get help with a question pertaining to an entire document.",
    ConversationCommand.Diagram: "Draw a flowchart, diagram, or any other visual representation best expressed with primitives like lines, rectangles, and text.",
    ConversationCommand.Research: "Do deep research on a topic. This will take longer than usual, but give a more detailed, comprehensive answer.",
}

command_descriptions_for_agent = {
    ConversationCommand.General: "Agent can use the agents knowledge base and general knowledge.",
    ConversationCommand.Notes: "Agent can search the personal knowledge base for information, as well as its own.",
    ConversationCommand.Online: "Agent can search the internet for information.",
    ConversationCommand.Webpage: "Agent can read suggested web pages for information.",
    ConversationCommand.Summarize: "Agent can read an entire document. Agents knowledge base must be a single document.",
    ConversationCommand.Research: "Agent can do deep research on a topic.",
}

tool_descriptions_for_llm = {
    ConversationCommand.Default: "To use a mix of your internal knowledge and the user's personal knowledge, or if you don't entirely understand the query.",
    ConversationCommand.General: "To use when you can answer the question without any outside information or personal knowledge",
    ConversationCommand.Notes: "To search the user's personal knowledge base. Especially helpful if the question expects context from the user's notes or documents.",
    ConversationCommand.Online: "To search for the latest, up-to-date information from the internet. Note: **Questions about Khoj should always use this data source**",
    ConversationCommand.Webpage: "To use if the user has directly provided the webpage urls or you are certain of the webpage urls to read.",
    ConversationCommand.Code: "To run Python code in a Pyodide sandbox with no network access. Helpful when need to parse complex information, run complex calculations, create plaintext documents, and create charts with quantitative data. Only matplotlib, panda, numpy, scipy, bs4 and sympy external packages are available.",
    ConversationCommand.Summarize: "To retrieve an answer that depends on the entire document or a large text.",
}

function_calling_description_for_llm = {
    ConversationCommand.Notes: "To search the user's personal knowledge base. Especially helpful if the question expects context from the user's notes or documents.",
    ConversationCommand.Online: "To search the internet for information. Useful to get a quick, broad overview from the internet. Provide all relevant context to ensure new searches, not in previous iterations, are performed.",
    ConversationCommand.Webpage: "To extract information from webpages. Useful for more detailed research from the internet. Usually used when you know the webpage links to refer to. Share the webpage links and information to extract in your query.",
    ConversationCommand.Code: "To run Python code in a Pyodide sandbox with no network access. Helpful when need to parse complex information, run complex calculations, create plaintext documents, and create charts with quantitative data. Only matplotlib, panda, numpy, scipy, bs4 and sympy external packages are available.",
}

mode_descriptions_for_llm = {
    ConversationCommand.Image: "Use this if you are confident the user is requesting you to create a new picture based on their description. This DOES NOT support generating charts or graphs. It is for creative images.",
    ConversationCommand.Text: "Use this if a normal text response would be sufficient for accurately responding to the query or you don't feel strongly about the other modes.",
    ConversationCommand.Diagram: "Use this if the user is requesting a diagram or visual representation that requires primitives like lines, rectangles, and text. This does not work for charts, graphs, or quantitative data. It is for mind mapping, flowcharts, etc.",
}

mode_descriptions_for_agent = {
    ConversationCommand.Image: "Agent can generate images in response. It cannot not use this to generate charts and graphs.",
    ConversationCommand.Automation: "Agent can schedule a task to run at a scheduled date, time and frequency in response.",
    ConversationCommand.Text: "Agent can generate text in response.",
    ConversationCommand.Diagram: "Agent can generate a visual representation that requires primitives like lines, rectangles, and text.",
}


class ImageIntentType(Enum):
    """
    Chat message intent by Khoj for image responses.
    Marks the schema used to reference image in chat messages
    """

    # Images as Inline PNG
    TEXT_TO_IMAGE = "text-to-image"
    # Images as URLs
    TEXT_TO_IMAGE2 = "text-to-image2"
    # Images as Inline WebP
    TEXT_TO_IMAGE_V3 = "text-to-image-v3"


def generate_random_name():
    # List of adjectives and nouns to choose from
    adjectives = [
        "happy",
        "serendipitous",
        "exuberant",
        "calm",
        "brave",
        "scared",
        "energetic",
        "chivalrous",
        "kind",
        "suave",
    ]
    nouns = ["dog", "cat", "falcon", "whale", "turtle", "rabbit", "hamster", "snake", "spider", "elephant"]

    # Select two random words from the lists
    adjective = random.choice(adjectives)
    noun = random.choice(nouns)

    # Combine the words to form a name
    name = f"{adjective} {noun}"

    return name


def batcher(iterable, max_n):
    "Split an iterable into chunks of size max_n"
    it = iter(iterable)
    while True:
        chunk = list(islice(it, max_n))
        if not chunk:
            return
        yield (x for x in chunk if x is not None)


def is_env_var_true(env_var: str, default: str = "false") -> bool:
    """Get state of boolean environment variable"""
    return os.getenv(env_var, default).lower() == "true"


def in_debug_mode():
    """Check if Khoj is running in debug mode.
    Set KHOJ_DEBUG environment variable to true to enable debug mode."""
    return is_env_var_true("KHOJ_DEBUG")


def is_promptrace_enabled():
    """Check if Khoj is running with prompt tracing enabled.
    Set PROMPTRACE_DIR environment variable to prompt tracing path to enable it."""
    return not is_none_or_empty(os.getenv("PROMPTRACE_DIR"))


def is_valid_url(url: str) -> bool:
    """Check if a string is a valid URL"""
    try:
        result = urlparse(url.strip())
        return all([result.scheme, result.netloc])
    except:
        return False


def is_internet_connected():
    try:
        response = requests.head("https://www.google.com")
        return response.status_code == 200
    except:
        return False


def is_internal_url(url: str) -> bool:
    """
    Check if a URL is likely to be internal/non-public.

    Args:
    url (str): The URL to check.

    Returns:
    bool: True if the URL is likely internal, False otherwise.
    """
    try:
        parsed_url = urllib.parse.urlparse(url)
        hostname = parsed_url.hostname

        # Check for localhost
        if hostname in ["localhost", "127.0.0.1", "::1"]:
            return True

        # Check for IP addresses in private ranges
        try:
            ip = ipaddress.ip_address(hostname)
            return ip.is_private
        except ValueError:
            pass  # Not an IP address, continue with other checks

        # Check for common internal TLDs
        internal_tlds = [".local", ".internal", ".private", ".corp", ".home", ".lan"]
        if any(hostname.endswith(tld) for tld in internal_tlds):
            return True

        # Check for URLs without a TLD
        if "." not in hostname:
            return True

        return False
    except Exception:
        # If we can't parse the URL or something else goes wrong, assume it's not internal
        return False


def convert_image_to_webp(image_bytes):
    """Convert image bytes to webp format for faster loading"""
    image_io = io.BytesIO(image_bytes)
    with Image.open(image_io) as original_image:
        webp_image_io = io.BytesIO()
        original_image.save(webp_image_io, "WEBP")

        # Encode the WebP image back to base64
        webp_image_bytes = webp_image_io.getvalue()
        webp_image_io.close()
        return webp_image_bytes


def truncate_code_context(original_code_results: dict[str, Any], max_chars=10000) -> dict[str, Any]:
    """
    Truncate large output files and drop image file data from code results.
    """
    # Create a deep copy of the code results to avoid modifying the original data
    code_results = copy.deepcopy(original_code_results)
    for code_result in code_results.values():
        for idx, output_file in enumerate(code_result["results"]["output_files"]):
            # Drop image files from code results
            if Path(output_file["filename"]).suffix in {".png", ".jpg", ".jpeg", ".webp"}:
                code_result["results"]["output_files"][idx] = {
                    "filename": output_file["filename"],
                    "b64_data": "[placeholder for generated image data for brevity]",
                }
            # Truncate large output files
            elif len(output_file["b64_data"]) > max_chars:
                code_result["results"]["output_files"][idx] = {
                    "filename": output_file["filename"],
                    "b64_data": output_file["b64_data"][:max_chars] + "...",
                }
    return code_results


@lru_cache
def tz_to_cc_map() -> dict[str, str]:
    """Create a mapping of timezone to country code"""
    timezone_country = {}
    for countrycode in country_timezones:
        timezones = country_timezones[countrycode]
        for timezone in timezones:
            timezone_country[timezone] = countrycode
    return timezone_country


def get_country_code_from_timezone(tz: str) -> str:
    """Get country code from timezone"""
    return tz_to_cc_map().get(tz, "US")


def get_country_name_from_timezone(tz: str) -> str:
    """Get country name from timezone"""
    return country_names.get(get_country_code_from_timezone(tz), "United States")


def get_cost_of_chat_message(model_name: str, input_tokens: int = 0, output_tokens: int = 0, prev_cost: float = 0.0):
    """
    Calculate cost of chat message based on input and output tokens
    """

    # Calculate cost of input and output tokens. Costs are per million tokens
    input_cost = constants.model_to_cost.get(model_name, {}).get("input", 0) * (input_tokens / 1e6)
    output_cost = constants.model_to_cost.get(model_name, {}).get("output", 0) * (output_tokens / 1e6)

    return input_cost + output_cost + prev_cost


def get_chat_usage_metrics(
    model_name: str, input_tokens: int = 0, output_tokens: int = 0, usage: dict = {}, cost: float = None
):
    """
    Get usage metrics for chat message based on input and output tokens and cost
    """
    prev_usage = usage or {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    return {
        "input_tokens": prev_usage["input_tokens"] + input_tokens,
        "output_tokens": prev_usage["output_tokens"] + output_tokens,
        "cost": cost or get_cost_of_chat_message(model_name, input_tokens, output_tokens, prev_cost=prev_usage["cost"]),
    }


def get_openai_client(api_key: str, api_base_url: str) -> Union[openai.OpenAI, openai.AzureOpenAI]:
    """Get OpenAI or AzureOpenAI client based on the API Base URL"""
    parsed_url = urlparse(api_base_url)
    if parsed_url.hostname and parsed_url.hostname.endswith(".openai.azure.com"):
        client = openai.AzureOpenAI(
            api_key=api_key,
            azure_endpoint=api_base_url,
            api_version="2024-10-21",
        )
    else:
        client = openai.OpenAI(
            api_key=api_key,
            base_url=api_base_url,
        )
    return client


def normalize_email(email: str, check_deliverability=False) -> tuple[str, bool]:
    """Normalize, validate and check deliverability of email address"""
    lower_email = email.lower()
    try:
        valid_email = validate_email(lower_email, check_deliverability=check_deliverability)
        return valid_email.normalized, True
    except (EmailNotValidError, EmailUndeliverableError):
        return lower_email, False
