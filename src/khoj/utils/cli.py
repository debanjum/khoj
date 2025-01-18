import argparse
import logging
import os
import pathlib
from importlib.metadata import version

logger = logging.getLogger(__name__)

from khoj.migrations.migrate_offline_chat_default_model import (
    migrate_offline_chat_default_model,
)
from khoj.migrations.migrate_offline_chat_schema import migrate_offline_chat_schema
from khoj.migrations.migrate_offline_model import migrate_offline_model
from khoj.migrations.migrate_processor_config_openai import (
    migrate_processor_conversation_schema,
)
from khoj.migrations.migrate_server_pg import migrate_server_pg
from khoj.migrations.migrate_version import migrate_config_to_version
from khoj.utils.helpers import in_debug_mode, is_env_var_true, resolve_absolute_path
from khoj.utils.yaml import parse_config_from_file


def cli(args=None):
    # Setup Argument Parser for the Commandline Interface
    parser = argparse.ArgumentParser(description="Start Khoj; An AI personal assistant for your Digital Brain")
    parser.add_argument(
        "--config-file", default="~/.khoj/khoj.yml", type=pathlib.Path, help="YAML file to configure Khoj"
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        default=False,
        help="Regenerate model embeddings from source files. Default: false",
    )
    parser.add_argument("--verbose", "-v", action="count", default=0, help="Show verbose conversion logs. Default: 0")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="Host address of the server. Default: 127.0.0.1")
    parser.add_argument("--port", "-p", type=int, default=42110, help="Port of the server. Default: 42110")
    parser.add_argument(
        "--socket",
        type=pathlib.Path,
        help="Path to UNIX socket for server. Use to run server behind reverse proxy. Default: /tmp/uvicorn.sock",
    )
    parser.add_argument("--sslcert", type=str, help="Path to SSL certificate file")
    parser.add_argument("--sslkey", type=str, help="Path to SSL key file")
    parser.add_argument("--version", "-V", action="store_true", help="Print the installed Khoj version and exit")
    parser.add_argument(
        "--disable-chat-on-gpu", action="store_true", default=False, help="Disable using GPU for the offline chat model"
    )
    parser.add_argument(
        "--anonymous-mode",
        action="store_true",
        default=False,
        help="Run Khoj in anonymous mode. This does not require any login for connecting users.",
    )
    parser.add_argument(
        "--controlled-access",
        action="store_true",
        default=False,
        help="Run with database authentication but no OAuth/Google login. Requires admin to create users.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        default=False,
        help="Start Khoj in non-interactive mode. Assumes interactive shell unavailable for config. E.g when run via Docker.",
    )

    args, remaining_args = parser.parse_known_args(args)

    if len(remaining_args) > 0:
        logger.info(f"⚠️  Ignoring unknown commandline args: {remaining_args}")

    # Set default values for arguments
    args.chat_on_gpu = not args.disable_chat_on_gpu

    args.version_no = version("khoj")
    if args.version:
        # Show version of khoj installed and exit
        print(args.version_no)
        exit(0)

    # Normalize config_file path to absolute path
    args.config_file = resolve_absolute_path(args.config_file)

    if not args.config_file.exists():
        args.config = None
    else:
        args = run_migrations(args)
        args.config = parse_config_from_file(args.config_file)
        if is_env_var_true("KHOJ_TELEMETRY_DISABLE") or in_debug_mode():
            args.config.app.should_log_telemetry = False

    return args


def run_migrations(args):
    migrations = [
        migrate_config_to_version,
        migrate_processor_conversation_schema,
        migrate_offline_model,
        migrate_offline_chat_schema,
        migrate_offline_chat_default_model,
        migrate_server_pg,
    ]
    for migration in migrations:
        args = migration(args)
    return args
