""" Main module for Khoj
   isort:skip_file
"""

from contextlib import redirect_stdout
import logging
import io
import os
import atexit
import sys
import locale

from rich.logging import RichHandler
import threading
import warnings
from importlib.metadata import version

from khoj.utils.helpers import in_debug_mode, is_env_var_true

# Ignore non-actionable warnings
warnings.filterwarnings("ignore", message=r"snapshot_download.py has been made private", category=FutureWarning)
warnings.filterwarnings("ignore", message=r"legacy way to download files from the HF hub,", category=FutureWarning)


import uvicorn
import django
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import schedule

from django.core.asgi import get_asgi_application
from django.core.management import call_command

# Initialize Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "khoj.app.settings")
django.setup()

# Setup Logger
rich_handler = RichHandler(rich_tracebacks=True)
rich_handler.setFormatter(fmt=logging.Formatter(fmt="%(name)s: %(message)s", datefmt="[%H:%M:%S.%f]"))
logging.basicConfig(handlers=[rich_handler])

logging.getLogger("uvicorn.error").setLevel(logging.INFO)

logger = logging.getLogger("khoj")

# Initialize Django Database
db_migrate_output = io.StringIO()
with redirect_stdout(db_migrate_output):
    call_command("migrate", "--noinput")

# Initialize Django Static Files
collectstatic_output = io.StringIO()
with redirect_stdout(collectstatic_output):
    call_command("collectstatic", "--noinput")

# Initialize the Application Server
if in_debug_mode():
    app = FastAPI(debug=True)
else:
    app = FastAPI(docs_url=None)  # Disable Swagger UI in production

# Get Django Application
django_app = get_asgi_application()

# Add CORS middleware
KHOJ_DOMAIN = os.getenv("KHOJ_DOMAIN", "app.khoj.dev")
scheme = "https" if not is_env_var_true("KHOJ_NO_HTTPS") else "http"
custom_origins = [f"{scheme}://{KHOJ_DOMAIN.strip()}", f"{scheme}://{KHOJ_DOMAIN.strip()}:*"]
default_origins = [
    "app://obsidian.md",  # To allow access from Obsidian desktop app
    "capacitor://localhost",  # To allow access from Obsidian iOS app using Capacitor.JS
    "http://localhost",  # To allow access from Obsidian Android app
    "http://localhost:*",  # To allow access from localhost
    "http://127.0.0.1:*",  # To allow access from localhost
    "app://khoj.dev",  # To allow access from Khoj desktop app
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=default_origins + custom_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set Locale
locale.setlocale(locale.LC_ALL, "")

# We import these packages after setting up Django so that Django features are accessible to the app.
from khoj.configure import configure_routes, initialize_server, configure_middleware
from khoj.utils import state
from khoj.utils.cli import cli
from khoj.utils.initialization import initialization
from khoj.database.adapters import ProcessLockAdapters
from khoj.database.models import ProcessLock

from django.db.utils import IntegrityError

SCHEDULE_LEADER_NAME = ProcessLock.Operation.SCHEDULE_LEADER


def shutdown_scheduler():
    logger.info("🌑 Shutting down Khoj")

    if state.schedule_leader_process_lock:
        logger.info("🔓 Schedule Leader released")
        ProcessLockAdapters.remove_process_lock(state.schedule_leader_process_lock)

    try:
        state.scheduler.shutdown()
    except Exception as e:
        logger.debug(f"Did not shutdown scheduler: {e}")


def run(should_start_server=True):
    # Turn Tokenizers Parallelism Off. App does not support it.
    os.environ["TOKENIZERS_PARALLELISM"] = "false"

    # Load config from CLI
    state.cli_args = sys.argv[1:]
    args = cli(state.cli_args)
    set_state(args)

    # Set Logging Level
    if args.verbose == 0:
        logger.setLevel(logging.INFO)
    elif args.verbose >= 1:
        logger.setLevel(logging.DEBUG)

    logger.info(f"🚒 Initializing Khoj v{state.khoj_version}")
    logger.info(f"📦 Initializing DB:\n{db_migrate_output.getvalue().strip()}")
    logger.debug(f"🌍 Initializing Web Client:\n{collectstatic_output.getvalue().strip()}")

    initialization(not args.non_interactive)

    # Create app directory, if it doesn't exist
    state.config_file.parent.mkdir(parents=True, exist_ok=True)

    # Set Log File
    fh = logging.FileHandler(state.config_file.parent / "khoj.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    logger.info("🌘 Starting Khoj")

    # Setup task scheduler
    poll_task_scheduler()

    # Setup Background Scheduler
    from django_apscheduler.jobstores import DjangoJobStore

    state.scheduler = BackgroundScheduler(
        {
            "apscheduler.timezone": "UTC",
            "apscheduler.job_defaults.misfire_grace_time": "60",  # Useful to run scheduled jobs even when worker delayed because it was busy or down
            "apscheduler.job_defaults.coalesce": "true",  # Combine multiple jobs into one if they are scheduled at the same time
        }
    )
    state.scheduler.add_jobstore(DjangoJobStore(), "default")

    # We use this mechanism to only elect one schedule leader in a distributed environment. This one will be responsible for actually executing the scheduled tasks. The others will still be capable of adding and removing tasks, but they will not execute them. This is to decrease the overall burden on the database and the system.
    try:
        schedule_leader_process_lock = ProcessLockAdapters.get_process_lock(SCHEDULE_LEADER_NAME)
        if schedule_leader_process_lock:
            logger.info("🔒 Schedule Leader is already running")
            state.scheduler.start(paused=True)
        else:
            logger.info("🔒 Schedule Leader elected")
            created_process_lock = ProcessLockAdapters.set_process_lock(
                SCHEDULE_LEADER_NAME, max_duration_in_seconds=43200
            )
            state.scheduler.start()
            state.schedule_leader_process_lock = created_process_lock
    except IntegrityError:
        logger.info("🔒 Schedule Leader running elsewhere")
        state.scheduler.start(paused=True)
    finally:
        logger.info("Started Background Scheduler")

    # Start Server
    configure_routes(app)

    #  Mount Django and Static Files
    app.mount("/server", django_app, name="server")
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    if not os.path.exists(static_dir):
        os.mkdir(static_dir)
    app.mount(f"/static", StaticFiles(directory=static_dir), name=static_dir)

    # Configure Middleware
    configure_middleware(app, state.ssl_config)

    initialize_server(args.config)

    # If the server is started through gunicorn (external to the script), don't start the server
    if should_start_server:
        start_server(app, host=args.host, port=args.port, socket=args.socket)
        # Teardown
        shutdown_scheduler()


def set_state(args):
    state.config_file = args.config_file
    state.config = args.config
    state.verbose = args.verbose
    state.host = args.host
    state.port = args.port
    state.ssl_config = (
        {"ssl_certfile": args.sslcert, "ssl_keyfile": args.sslkey} if args.sslcert and args.sslkey else None
    )

    # Handle authentication modes
    if args.anonymous_mode and args.controlled_access:
        logger.error("Cannot use both --anonymous-mode and --controlled-access")
        sys.exit(1)

    # Handle authentication modes - CLI flags override environment variables
    state.anonymous_mode = args.anonymous_mode or is_env_var_true("KHOJ_ANONYMOUS_MODE")
    state.controlled_access_mode = args.controlled_access or is_env_var_true("KHOJ_CONTROLLED_ACCESS")

    # Ensure modes are mutually exclusive
    if state.anonymous_mode and state.controlled_access_mode:
        logger.error("Cannot use both anonymous mode and controlled access mode")
        sys.exit(1)
    state.khoj_version = version("khoj")
    state.chat_on_gpu = args.chat_on_gpu


def start_server(app, host=None, port=None, socket=None):
    logger.info("🌖 Khoj is ready to use")
    if socket:
        uvicorn.run(app, proxy_headers=True, uds=socket, log_level="debug", use_colors=True, log_config=None)
    else:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="debug" if state.verbose > 1 else "info",
            use_colors=True,
            log_config=None,
            timeout_keep_alive=60,
            **state.ssl_config if state.ssl_config else {},
        )
    logger.info("🌒 Stopping Khoj")


def poll_task_scheduler():
    timer_thread = threading.Timer(60.0, poll_task_scheduler)
    timer_thread.daemon = True
    timer_thread.start()
    schedule.run_pending()


if __name__ == "__main__":
    run()
else:
    if "gunicorn" in sys.modules:
        run(should_start_server=False)
        atexit.register(shutdown_scheduler)
