import os
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

from apscheduler.schedulers.background import BackgroundScheduler
from openai import OpenAI
from whisper import Whisper

from khoj.database.models import ProcessLock
from khoj.processor.embeddings import CrossEncoderModel, EmbeddingsModel
from khoj.utils import config as utils_config
from khoj.utils.config import OfflineChatProcessorModel, SearchModels
from khoj.utils.helpers import LRU, get_device, is_env_var_true
from khoj.utils.rawconfig import FullConfig

# Application Global State
config = FullConfig()
search_models = SearchModels()
embeddings_model: Dict[str, EmbeddingsModel] = None
cross_encoder_model: Dict[str, CrossEncoderModel] = None
openai_client: OpenAI = None
offline_chat_processor_config: OfflineChatProcessorModel = None
whisper_model: Whisper = None
config_file: Path = None
verbose: int = 0
host: str = None
port: int = None
ssl_config: Dict[str, str] = None
cli_args: List[str] = None
query_cache: Dict[str, LRU] = defaultdict(LRU)
chat_lock = threading.Lock()
SearchType = utils_config.SearchType
scheduler: BackgroundScheduler = None
schedule_leader_process_lock: ProcessLock = None
telemetry: List[Dict[str, str]] = []
telemetry_disabled: bool = is_env_var_true("KHOJ_TELEMETRY_DISABLE")
khoj_version: str = None
device = get_device()
chat_on_gpu: bool = True
anonymous_mode: bool = False
controlled_access_mode: bool = False
pretrained_tokenizers: Dict[str, Any] = dict()
billing_enabled: bool = (
    os.getenv("STRIPE_API_KEY") is not None
    and os.getenv("STRIPE_SIGNING_SECRET") is not None
    and os.getenv("KHOJ_CLOUD_SUBSCRIPTION_URL") is not None
)
