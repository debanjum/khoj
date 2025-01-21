import json
import logging
import os
from datetime import datetime
from enum import Enum
from functools import wraps
from typing import Optional

import openai
import requests
import schedule
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections, connections
from django.utils.timezone import make_aware
from fastapi import Response
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    SimpleUser,
    UnauthenticatedUser,
)
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from khoj.database.adapters import (
    AgentAdapters,
    ClientApplicationAdapters,
    ConversationAdapters,
    ProcessLockAdapters,
    aget_or_create_user_by_phone_number,
    aget_user_by_phone_number,
    ais_user_subscribed,
    delete_user_requests,
    get_all_users,
    get_or_create_search_models,
)
from khoj.database.models import ClientApplication, KhojUser, ProcessLock, Subscription
from khoj.processor.embeddings import CrossEncoderModel, EmbeddingsModel
from khoj.routers.api_content import configure_content, configure_search
from khoj.routers.twilio import is_twilio_enabled
from khoj.utils import constants, state
from khoj.utils.config import SearchType
from khoj.utils.fs_syncer import collect_files
from khoj.utils.helpers import is_none_or_empty, telemetry_disabled
from khoj.utils.rawconfig import FullConfig

logger = logging.getLogger(__name__)


class AuthenticatedKhojUser(SimpleUser):
    def __init__(self, user, client_app: Optional[ClientApplication] = None):
        self.object = user
        self.client_app = client_app
        super().__init__(user.username)


class AsyncCloseConnectionsMiddleware(BaseHTTPMiddleware):
    """
    Using this middleware to call close_old_connections() twice is a pretty yucky hack,
    as it appears that run_in_threadpool (used by Starlette/FastAPI) and sync_to_async
    (used by Django) have divergent behavior, ultimately acquiring the incorrect thread
    in mixed sync/async which has the effect of duplicating connections.
    We could fix the duplicate connections too if we normalized the thread behavior,
    but at minimum we need to clean up connections in each case to prevent persistent
    "InterfaceError: connection already closed" errors when the database connection is
    reset via a database restart or something -- so here we are!
    If we always use smart_sync_to_async(), this double calling isn't necessary, but
    depending on what levels of abstraction we introduce, we might silently break the
    assumptions. Better to be safe than sorry!
    Attribution: https://gist.github.com/bryanhelmig/6fb091f23c1a4b7462dddce51cfaa1ca
    """

    async def dispatch(self, request, call_next):
        await run_in_threadpool(close_old_connections)
        await sync_to_async(close_old_connections)()
        try:
            response = await call_next(request)
        finally:
            # in tests, use @override_settings(CLOSE_CONNECTIONS_AFTER_REQUEST=True)
            if getattr(settings, "CLOSE_CONNECTIONS_AFTER_REQUEST", False):
                await run_in_threadpool(connections.close_all)
                await sync_to_async(connections.close_all)()
        return response


class UserAuthenticationBackend(AuthenticationBackend):
    def __init__(
        self,
    ):
        from khoj.database.models import KhojApiUser, KhojUser

        self.khojuser_manager = KhojUser.objects
        self.khojapiuser_manager = KhojApiUser.objects
        self._initialize_default_user()
        super().__init__()

    def _initialize_default_user(self):
        if not self.khojuser_manager.filter(username="default").exists():
            default_user = self.khojuser_manager.create_user(
                username="default",
                email="default@example.com",
                password="default",
            )
            renewal_date = make_aware(datetime.strptime("2100-04-01", "%Y-%m-%d"))
            Subscription.objects.create(user=default_user, type=Subscription.Type.STANDARD, renewal_date=renewal_date)

    async def authenticate(self, request: HTTPConnection):
        current_user = request.session.get("user")
        if current_user and current_user.get("email"):
            user = (
                await self.khojuser_manager.filter(email=current_user.get("email"))
                .prefetch_related("subscription")
                .afirst()
            )
            if user:
                subscribed = await ais_user_subscribed(user)
                if subscribed:
                    return AuthCredentials(["authenticated", "premium"]), AuthenticatedKhojUser(user)
                return AuthCredentials(["authenticated"]), AuthenticatedKhojUser(user)

        # Request from Desktop, Emacs, Obsidian clients
        if len(request.headers.get("Authorization", "").split("Bearer ")) == 2:
            # Get bearer token from header
            bearer_token = request.headers["Authorization"].split("Bearer ")[1]
            # Get user owning token
            user_with_token = (
                await self.khojapiuser_manager.filter(token=bearer_token)
                .select_related("user")
                .prefetch_related("user__subscription")
                .afirst()
            )
            if user_with_token:
                subscribed = await ais_user_subscribed(user_with_token.user)
                if subscribed:
                    return AuthCredentials(["authenticated", "premium"]), AuthenticatedKhojUser(user_with_token.user)
                return AuthCredentials(["authenticated"]), AuthenticatedKhojUser(user_with_token.user)

        # Request from Whatsapp client
        client_id = request.query_params.get("client_id")
        if client_id:
            # Get the client secret, which is passed in the Authorization header
            client_secret = request.headers["Authorization"].split("Bearer ")[1]
            if not client_secret:
                return Response(
                    status_code=401,
                    content="Please provide a client secret in the Authorization header with a client_id query param.",
                )

            # Get the client application
            client_application = await ClientApplicationAdapters.aget_client_application_by_id(client_id, client_secret)
            if client_application is None:
                return AuthCredentials(), UnauthenticatedUser()
            # Get the identifier used for the user
            phone_number = request.query_params.get("phone_number")
            if is_none_or_empty(phone_number):
                return AuthCredentials(), UnauthenticatedUser()

            if not phone_number.startswith("+"):
                phone_number = f"+{phone_number}"

            create_if_not_exists = request.query_params.get("create_if_not_exists")
            if create_if_not_exists:
                user, is_new = await aget_or_create_user_by_phone_number(phone_number)
                if user and is_new:
                    logger.log(logging.INFO, f"🥳 New User Created: {user.uuid}")
            else:
                user = await aget_user_by_phone_number(phone_number)

            if user is None:
                return AuthCredentials(), UnauthenticatedUser()

            subscribed = await ais_user_subscribed(user)

            if subscribed:
                return AuthCredentials(["authenticated", "premium"]), AuthenticatedKhojUser(user, client_application)
            return AuthCredentials(["authenticated"]), AuthenticatedKhojUser(user, client_application)

        # No auth required if server in anonymous mode
        if state.anonymous_mode:
            user = await self.khojuser_manager.filter(username="default").prefetch_related("subscription").afirst()
            if user:
                return AuthCredentials(["authenticated", "premium"]), AuthenticatedKhojUser(user)

        return AuthCredentials(), UnauthenticatedUser()


def initialize_server(config: Optional[FullConfig]):
    try:
        configure_server(config, init=True)
    except Exception as e:
        logger.error(f"🚨 Failed to configure server on app load: {e}", exc_info=True)
        raise e


def clean_connections(func):
    """
    A decorator that ensures that Django database connections that have become unusable, or are obsolete, are closed
    before and after a method is executed (see: https://docs.djangoproject.com/en/dev/ref/databases/#general-notes
    for background).
    """

    @wraps(func)
    def func_wrapper(*args, **kwargs):
        close_old_connections()
        try:
            result = func(*args, **kwargs)
        finally:
            close_old_connections()

        return result

    return func_wrapper


def configure_server(
    config: FullConfig,
    regenerate: bool = False,
    search_type: Optional[SearchType] = None,
    init=False,
    user: KhojUser = None,
):
    # Update Config
    if config == None:
        logger.info(f"Initializing with default config.")
        config = FullConfig()
    state.config = config

    if ConversationAdapters.has_valid_ai_model_api():
        ai_model_api = ConversationAdapters.get_ai_model_api()
        state.openai_client = openai.OpenAI(api_key=ai_model_api.api_key, base_url=ai_model_api.api_base_url)

    # Initialize Search Models from Config and initialize content
    try:
        search_models = get_or_create_search_models()
        state.embeddings_model = dict()
        state.cross_encoder_model = dict()

        for model in search_models:
            state.embeddings_model.update(
                {
                    model.name: EmbeddingsModel(
                        model.bi_encoder,
                        model.embeddings_inference_endpoint,
                        model.embeddings_inference_endpoint_api_key,
                        model.embeddings_inference_endpoint_type,
                        query_encode_kwargs=model.bi_encoder_query_encode_config,
                        docs_encode_kwargs=model.bi_encoder_docs_encode_config,
                        model_kwargs=model.bi_encoder_model_config,
                    )
                }
            )
            state.cross_encoder_model.update(
                {
                    model.name: CrossEncoderModel(
                        model.cross_encoder,
                        model.cross_encoder_inference_endpoint,
                        model.cross_encoder_inference_endpoint_api_key,
                        model_kwargs=model.cross_encoder_model_config,
                    )
                }
            )

        state.SearchType = configure_search_types()
        state.search_models = configure_search(state.search_models, state.config.search_type)
        setup_default_agent(user)

        message = (
            "📡 Telemetry disabled"
            if telemetry_disabled(state.config.app, state.telemetry_disabled)
            else "📡 Telemetry enabled"
        )
        logger.info(message)

        if not init:
            initialize_content(user, regenerate, search_type)

    except Exception as e:
        logger.error(f"Failed to load some search models: {e}", exc_info=True)


def setup_default_agent(user: KhojUser):
    AgentAdapters.create_default_agent(user)


def initialize_content(user: KhojUser, regenerate: bool, search_type: Optional[SearchType] = None):
    # Initialize Content from Config
    if state.search_models:
        try:
            logger.info("📬 Updating content index...")
            all_files = collect_files(user=user)
            status = configure_content(
                user,
                all_files,
                regenerate,
                search_type,
            )
            if not status:
                raise RuntimeError("Failed to update content index")
        except Exception as e:
            raise e


def configure_routes(app):
    # Import APIs here to setup search types before while configuring server
    from khoj.routers.api import api
    from khoj.routers.api_agents import api_agents
    from khoj.routers.api_chat import api_chat
    from khoj.routers.api_content import api_content
    from khoj.routers.api_model import api_model
    from khoj.routers.notion import notion_router
    from khoj.routers.web_client import web_client

    app.include_router(api, prefix="/api")
    app.include_router(api_chat, prefix="/api/chat")
    app.include_router(api_agents, prefix="/api/agents")
    app.include_router(api_model, prefix="/api/model")
    app.include_router(api_content, prefix="/api/content")
    app.include_router(notion_router, prefix="/api/notion")
    app.include_router(web_client)

    if not state.anonymous_mode:
        from khoj.routers.auth import auth_router

        app.include_router(auth_router, prefix="/auth")
        logger.info("🔑 Enabled Authentication")

    if state.billing_enabled:
        from khoj.routers.api_subscription import subscription_router

        app.include_router(subscription_router, prefix="/api/subscription")
        logger.info("💳 Enabled Billing")

    if is_twilio_enabled():
        from khoj.routers.api_phone import api_phone

        app.include_router(api_phone, prefix="/api/phone")
        logger.info("📞 Enabled Twilio")


def configure_middleware(app, ssl_enabled: bool = False):
    class NextJsMiddleware(Middleware):
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "http" and scope["path"].startswith("/_next"):
                scope["path"] = "/static" + scope["path"]
            await self.app(scope, receive, send)

        def __init__(self, app: ASGIApp) -> None:
            super().__init__(app)
            self.app = app

    if ssl_enabled:
        app.add_middleware(HTTPSRedirectMiddleware)
    app.add_middleware(AsyncCloseConnectionsMiddleware)
    app.add_middleware(AuthenticationMiddleware, backend=UserAuthenticationBackend())
    app.add_middleware(NextJsMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=os.environ.get("KHOJ_DJANGO_SECRET_KEY", "!secret"))


def update_content_index():
    for user in get_all_users():
        all_files = collect_files(user=user)
        success = configure_content(user, all_files)
    if not success:
        raise RuntimeError("Failed to update content index")
    logger.info("📪 Content index updated via Scheduler")


@schedule.repeat(schedule.every(22).to(25).hours)
@clean_connections
def update_content_index_regularly():
    ProcessLockAdapters.run_with_lock(
        update_content_index, ProcessLock.Operation.INDEX_CONTENT, max_duration_in_seconds=60 * 60 * 2
    )


def configure_search_types():
    # Extract core search types
    core_search_types = {e.name: e.value for e in SearchType}

    # Dynamically generate search type enum by merging core search types with configured plugin search types
    return Enum("SearchType", core_search_types)


@schedule.repeat(schedule.every(2).minutes)
@clean_connections
def upload_telemetry():
    if telemetry_disabled(state.config.app, state.telemetry_disabled) or not state.telemetry:
        return

    try:
        logger.info(f"📡 Uploading telemetry to {constants.telemetry_server}...")
        logger.debug(f"Telemetry state:\n{state.telemetry}")
        for log in state.telemetry:
            for field in log:
                # Check if the value for the field is JSON serializable
                if log[field] is None:
                    log[field] = ""
                try:
                    json.dumps(log[field])
                except TypeError:
                    log[field] = str(log[field])
        response = requests.post(constants.telemetry_server, json=state.telemetry)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"📡 Error uploading telemetry: {e}", exc_info=True)
    else:
        state.telemetry = []


@schedule.repeat(schedule.every(31).minutes)
@clean_connections
def delete_old_user_requests():
    num_deleted = delete_user_requests()
    logger.debug(f"🗑️ Deleted {num_deleted[0]} day-old user requests")


@schedule.repeat(schedule.every(17).minutes)
@clean_connections
def wakeup_scheduler():
    # Wake up the scheduler to ensure it runs the scheduled tasks. This is because the elected leader may not always be aware of tasks scheduled on other workers.
    TWELVE_HOURS = 43200

    # If the worker currently possesses a process lock, check if it is valid.

    if state.schedule_leader_process_lock:
        if not ProcessLockAdapters.is_process_locked(state.schedule_leader_process_lock):
            state.schedule_leader_process_lock = None
            state.scheduler.pause()

    # Get the current process lock
    schedule_leader_process_lock = ProcessLockAdapters.get_process_lock(ProcessLock.Operation.SCHEDULE_LEADER)

    # Check if the process lock is still active. If not, create a new process lock. This worker will become the scheduler leader.
    if not schedule_leader_process_lock or not ProcessLockAdapters.is_process_locked(schedule_leader_process_lock):
        schedule_leader_process_lock = ProcessLockAdapters.set_process_lock(
            ProcessLock.Operation.SCHEDULE_LEADER, max_duration_in_seconds=TWELVE_HOURS
        )
        state.schedule_leader_process_lock = schedule_leader_process_lock
        state.scheduler.resume()
        logger.info("🔔 Scheduler leader process lock acquired")

    if state.schedule_leader_process_lock:
        state.scheduler.wakeup()
    else:
        # Make sure the other workers don't run the scheduled tasks
        state.scheduler.pause()
