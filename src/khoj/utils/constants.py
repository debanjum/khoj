from pathlib import Path
from typing import Dict

app_root_directory = Path(__file__).parent.parent.parent
web_directory = app_root_directory / "khoj/interface/web/"
next_js_directory = app_root_directory / "khoj/interface/built/"
pypi_static_directory = app_root_directory / "khoj/interface/compiled/"
assetlinks_file_path = web_directory / ".well-known/assetlinks.json"
empty_escape_sequences = "\n|\r|\t| "
app_env_filepath = "~/.khoj/env"
telemetry_server = "https://khoj.beta.haletic.com/v1/telemetry"
content_directory = "~/.khoj/content/"
default_offline_chat_models = [
    "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
    "bartowski/Llama-3.2-3B-Instruct-GGUF",
    "bartowski/gemma-2-9b-it-GGUF",
    "bartowski/gemma-2-2b-it-GGUF",
    "bartowski/Qwen2.5-14B-Instruct-GGUF",
]
default_openai_chat_models = ["gpt-4o-mini", "gpt-4o"]
default_gemini_chat_models = ["gemini-1.5-flash", "gemini-1.5-pro"]
default_anthropic_chat_models = ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022"]

empty_config = {
    "search-type": {
        "image": {"encoder": "sentence-transformers/clip-ViT-B-32", "model_directory": "~/.khoj/search/image/"},
    },
}

# default app config to use
default_config = {
    "search-type": {
        "image": {"encoder": "sentence-transformers/clip-ViT-B-32", "model_directory": "~/.khoj/search/image/"},
    },
}

model_to_cost: Dict[str, Dict[str, float]] = {
    # OpenAI Pricing: https://openai.com/api/pricing/
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "o1": {"input": 15.0, "output": 60.00},
    "o1-mini": {"input": 3.0, "output": 12.0},
    # Gemini Pricing: https://ai.google.dev/pricing
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-002": {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-pro-002": {"input": 1.25, "output": 5.00},
    # Anthropic Pricing: https://www.anthropic.com/pricing#anthropic-api_
    "claude-3-5-sonnet-20241022": {"input": 3.0, "output": 15.0},
    "claude-3-5-haiku-20241022": {"input": 1.0, "output": 5.0},
}
