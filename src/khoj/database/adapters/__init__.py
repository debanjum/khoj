import json
import logging
import math
import os
import random
import re
import secrets
import sys
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from functools import wraps
from typing import (
    Any,
    Callable,
    Coroutine,
    Iterable,
    List,
    Optional,
    ParamSpec,
    TypeVar,
)

import cron_descriptor
from apscheduler.job import Job
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.db.models import Prefetch, Q
from django.db.models.manager import BaseManager
from django.db.utils import IntegrityError
from django_apscheduler import util
from django_apscheduler.models import DjangoJob, DjangoJobExecution
from fastapi import HTTPException
from pgvector.django import CosineDistance
from torch import Tensor

from khoj.database.models import (
    Agent,
    AiModelApi,
    ChatModel,
    ClientApplication,
    Conversation,
    Entry,
    FileObject,
    GithubConfig,
    GithubRepoConfig,
    GoogleUser,
    KhojApiUser,
    KhojUser,
    NotionConfig,
    ProcessLock,
    PublicConversation,
    ReflectiveQuestion,
    SearchModelConfig,
    ServerChatSettings,
    SpeechToTextModelOptions,
    Subscription,
    TextToImageModelConfig,
    UserConversationConfig,
    UserRequests,
    UserTextToImageModelConfig,
    UserVoiceModelConfig,
    VoiceModelOption,
    WebScraper,
)
from khoj.processor.conversation import prompts
from khoj.search_filter.date_filter import DateFilter
from khoj.search_filter.file_filter import FileFilter
from khoj.search_filter.word_filter import WordFilter
from khoj.utils import state
from khoj.utils.config import OfflineChatProcessorModel
from khoj.utils.helpers import (
    generate_random_name,
    in_debug_mode,
    is_none_or_empty,
    normalize_email,
    timer,
)

logger = logging.getLogger(__name__)


LENGTH_OF_FREE_TRIAL = 7  #


class SubscriptionState(Enum):
    TRIAL = "trial"
    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    EXPIRED = "expired"
    INVALID = "invalid"


P = ParamSpec("P")
T = TypeVar("T")


def require_valid_user(func: Callable[P, T]) -> Callable[P, T]:
    @wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        # Extract user from args/kwargs
        user = next((arg for arg in args if isinstance(arg, KhojUser)), None)
        if not user:
            user = next((val for val in kwargs.values() if isinstance(val, KhojUser)), None)

        # Throw error if user is not found
        if not user:
            raise ValueError("Khoj user argument required but not provided.")

        return func(*args, **kwargs)

    return sync_wrapper


def arequire_valid_user(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(func)
    async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        # Extract user from args/kwargs
        user = next((arg for arg in args if isinstance(arg, KhojUser)), None)
        if not user:
            user = next((v for v in kwargs.values() if isinstance(v, KhojUser)), None)

        # Throw error if user is not found
        if not user:
            raise ValueError("Khoj user argument required but not provided.")

        return await func(*args, **kwargs)

    return async_wrapper


@arequire_valid_user
async def set_notion_config(token: str, user: KhojUser):
    notion_config = await NotionConfig.objects.filter(user=user).afirst()
    if not notion_config:
        notion_config = await NotionConfig.objects.acreate(token=token, user=user)
    else:
        notion_config.token = token
        await notion_config.asave()
    return notion_config


@require_valid_user
def create_khoj_token(user: KhojUser, name=None):
    "Create Khoj API key for user"
    token = f"kk-{secrets.token_urlsafe(32)}"
    name = name or f"{generate_random_name().title()}"
    return KhojApiUser.objects.create(token=token, user=user, name=name)


@arequire_valid_user
async def acreate_khoj_token(user: KhojUser, name=None):
    "Create Khoj API key for user"
    token = f"kk-{secrets.token_urlsafe(32)}"
    name = name or f"{generate_random_name().title()}"
    return await KhojApiUser.objects.acreate(token=token, user=user, name=name)


@require_valid_user
def get_khoj_tokens(user: KhojUser):
    "Get all Khoj API keys for user"
    return list(KhojApiUser.objects.filter(user=user))


@arequire_valid_user
async def delete_khoj_token(user: KhojUser, token: str):
    "Delete Khoj API Key for user"
    await KhojApiUser.objects.filter(token=token, user=user).adelete()


async def get_or_create_user(token: dict) -> KhojUser:
    user = await get_user_by_token(token)
    if not user:
        user = await create_user_by_google_token(token)
    return user


async def aget_or_create_user_by_phone_number(phone_number: str) -> tuple[KhojUser, bool]:
    is_new = False
    if is_none_or_empty(phone_number):
        return None, is_new
    user = await aget_user_by_phone_number(phone_number)
    if not user:
        user = await acreate_user_by_phone_number(phone_number)
        is_new = True
    return user, is_new


@arequire_valid_user
async def aset_user_phone_number(user: KhojUser, phone_number: str) -> KhojUser:
    if is_none_or_empty(phone_number):
        return None
    phone_number = phone_number.strip()
    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"
    existing_user_with_phone_number = await aget_user_by_phone_number(phone_number)
    if existing_user_with_phone_number and existing_user_with_phone_number.id != user.id:
        if is_none_or_empty(existing_user_with_phone_number.email):
            # Transfer conversation history to the new user. If they don't have an associated email, they are effectively a new user
            async for conversation in Conversation.objects.filter(user=existing_user_with_phone_number).aiterator():
                conversation.user = user
                await conversation.asave()

            await existing_user_with_phone_number.adelete()
        else:
            raise HTTPException(status_code=400, detail="Phone number already exists")

    user.phone_number = phone_number
    await user.asave()
    return user


@arequire_valid_user
async def aremove_phone_number(user: KhojUser) -> KhojUser:
    user.phone_number = None
    user.verified_phone_number = False
    await user.asave()
    return user


async def acreate_user_by_phone_number(phone_number: str) -> KhojUser:
    if is_none_or_empty(phone_number):
        return None
    user, _ = await KhojUser.objects.filter(phone_number=phone_number).aupdate_or_create(
        defaults={"username": phone_number, "phone_number": phone_number}
    )
    await user.asave()

    user_subscription = await Subscription.objects.filter(user=user).afirst()
    if not user_subscription:
        await Subscription.objects.acreate(user=user, type=Subscription.Type.STANDARD)

    return user


async def aget_or_create_user_by_email(input_email: str) -> tuple[KhojUser, bool]:
    email, is_valid_email = normalize_email(input_email)
    is_existing_user = await KhojUser.objects.filter(email=email).aexists()
    # Validate email address of new users
    if not is_existing_user and not is_valid_email:
        logger.error(f"Account creation failed. Invalid email address: {email}")
        return None, False

    user, is_new = await KhojUser.objects.filter(email=email).aupdate_or_create(
        defaults={"username": email, "email": email}
    )

    # Generate a secure 6-digit numeric code
    user.email_verification_code = f"{secrets.randbelow(1000000):06}"
    user.email_verification_code_expiry = datetime.now(tz=timezone.utc) + timedelta(minutes=5)
    await user.asave()

    user_subscription = await Subscription.objects.filter(user=user).afirst()
    if not user_subscription:
        await Subscription.objects.acreate(user=user, type=Subscription.Type.STANDARD)

    return user, is_new


@arequire_valid_user
async def astart_trial_subscription(user: KhojUser) -> Subscription:
    subscription = await Subscription.objects.filter(user=user).afirst()
    if not subscription:
        raise HTTPException(status_code=400, detail="User does not have a subscription")

    if subscription.type == Subscription.Type.TRIAL:
        raise HTTPException(status_code=400, detail="User already has a trial subscription")

    if subscription.enabled_trial_at:
        raise HTTPException(status_code=400, detail="User already has a trial subscription")

    subscription.type = Subscription.Type.TRIAL
    subscription.enabled_trial_at = datetime.now(tz=timezone.utc)
    subscription.renewal_date = datetime.now(tz=timezone.utc) + timedelta(days=LENGTH_OF_FREE_TRIAL)
    await subscription.asave()
    return subscription


async def aget_user_validated_by_email_verification_code(code: str, email: str) -> tuple[Optional[KhojUser], bool]:
    # Normalize the email address
    normalized_email, _ = normalize_email(email)

    # Check if verification code exists for the user
    user = await KhojUser.objects.filter(email_verification_code=code, email=normalized_email).afirst()
    if not user:
        return None, False

    # Check if the code has expired
    if user.email_verification_code_expiry < datetime.now(tz=timezone.utc):
        return user, True

    user.email_verification_code = None
    user.verified_email = True
    await user.asave()

    return user, False


async def create_user_by_google_token(token: dict) -> KhojUser:
    user, _ = await KhojUser.objects.filter(email=token.get("email")).aupdate_or_create(
        defaults={"username": token.get("email"), "email": token.get("email")}
    )
    user.verified_email = True
    await user.asave()

    await GoogleUser.objects.acreate(
        sub=token.get("sub"),
        azp=token.get("azp"),
        email=token.get("email"),
        name=token.get("name"),
        given_name=token.get("given_name"),
        family_name=token.get("family_name"),
        picture=token.get("picture"),
        locale=token.get("locale"),
        user=user,
    )

    user_subscription = await Subscription.objects.filter(user=user).afirst()
    if not user_subscription:
        await Subscription.objects.acreate(user=user, type=Subscription.Type.STANDARD)

    return user


@require_valid_user
def set_user_name(user: KhojUser, first_name: str, last_name: str) -> KhojUser:
    user.first_name = first_name
    user.last_name = last_name
    user.save()
    return user


@require_valid_user
def get_user_name(user: KhojUser):
    full_name = user.get_full_name()
    if not is_none_or_empty(full_name):
        return full_name
    google_profile: GoogleUser = GoogleUser.objects.filter(user=user).first()
    if google_profile:
        return google_profile.given_name

    return None


@require_valid_user
def get_user_photo(user: KhojUser):
    google_profile: GoogleUser = GoogleUser.objects.filter(user=user).first()
    if google_profile:
        return google_profile.picture

    return None


def get_user_subscription(email: str) -> Optional[Subscription]:
    return Subscription.objects.filter(user__email=email).first()


async def set_user_subscription(
    email: str, is_recurring=None, renewal_date=None, type="standard"
) -> tuple[Optional[Subscription], bool]:
    # Get or create the user object and their subscription
    user, is_new = await aget_or_create_user_by_email(email)
    if not user:
        return None, is_new
    user_subscription = await Subscription.objects.filter(user=user).afirst()

    # Update the user subscription state
    user_subscription.type = type
    if is_recurring is not None:
        user_subscription.is_recurring = is_recurring
    if renewal_date is None:
        user_subscription.renewal_date = None
    elif renewal_date is not None:
        user_subscription.renewal_date = renewal_date
    await user_subscription.asave()
    return user_subscription, is_new


def subscription_to_state(subscription: Subscription) -> str:
    if not subscription:
        return SubscriptionState.INVALID.value
    elif subscription.type == Subscription.Type.TRIAL:
        # Check if the trial has expired
        if not subscription.renewal_date:
            # If the renewal date is not set, set it to the current date + trial length and evaluate
            subscription.renewal_date = subscription.created_at + timedelta(days=LENGTH_OF_FREE_TRIAL)
            subscription.save()

        if subscription.renewal_date and datetime.now(tz=timezone.utc) > subscription.renewal_date:
            return SubscriptionState.EXPIRED.value
        return SubscriptionState.TRIAL.value
    elif subscription.is_recurring and subscription.renewal_date > datetime.now(tz=timezone.utc):
        return SubscriptionState.SUBSCRIBED.value
    elif not subscription.is_recurring and subscription.renewal_date is None:
        return SubscriptionState.EXPIRED.value
    elif not subscription.is_recurring and subscription.renewal_date > datetime.now(tz=timezone.utc):
        return SubscriptionState.UNSUBSCRIBED.value
    elif not subscription.is_recurring and subscription.renewal_date < datetime.now(tz=timezone.utc):
        return SubscriptionState.EXPIRED.value
    return SubscriptionState.INVALID.value


def get_user_subscription_state(email: str) -> str:
    """Get subscription state of user
    Valid state transitions: trial -> subscribed <-> unsubscribed OR expired
    """
    user_subscription = Subscription.objects.filter(user__email=email).first()
    return subscription_to_state(user_subscription)


@arequire_valid_user
async def aget_user_subscription_state(user: KhojUser) -> str:
    """Get subscription state of user
    Valid state transitions: trial -> subscribed <-> unsubscribed OR expired
    """
    user_subscription = await Subscription.objects.filter(user=user).afirst()
    return await sync_to_async(subscription_to_state)(user_subscription)


@arequire_valid_user
async def ais_user_subscribed(user: KhojUser) -> bool:
    """
    Get whether the user is subscribed
    """
    if not state.billing_enabled or state.anonymous_mode:
        return True

    subscription_state = await aget_user_subscription_state(user)
    subscribed = (
        subscription_state == SubscriptionState.SUBSCRIBED.value
        or subscription_state == SubscriptionState.TRIAL.value
        or subscription_state == SubscriptionState.UNSUBSCRIBED.value
    )
    return subscribed


@require_valid_user
def is_user_subscribed(user: KhojUser) -> bool:
    """
    Get whether the user is subscribed
    """
    if not state.billing_enabled or state.anonymous_mode:
        return True

    subscription_state = get_user_subscription_state(user.email)
    subscribed = (
        subscription_state == SubscriptionState.SUBSCRIBED.value
        or subscription_state == SubscriptionState.TRIAL.value
        or subscription_state == SubscriptionState.UNSUBSCRIBED.value
    )
    return subscribed


async def aget_user_by_email(email: str) -> KhojUser:
    return await KhojUser.objects.filter(email=email).afirst()


def get_user_by_email(email: str) -> KhojUser:
    return KhojUser.objects.filter(email=email).first()


async def aget_user_by_uuid(uuid: str) -> KhojUser:
    return await KhojUser.objects.filter(uuid=uuid).afirst()


async def get_user_by_token(token: dict) -> KhojUser:
    google_user = await GoogleUser.objects.filter(sub=token.get("sub")).select_related("user").afirst()
    if not google_user:
        return None
    return google_user.user


async def aget_user_by_phone_number(phone_number: str) -> KhojUser:
    if is_none_or_empty(phone_number):
        return None
    matched_user = await KhojUser.objects.filter(phone_number=phone_number).prefetch_related("subscription").afirst()

    if not matched_user:
        return None

    # If the user with this phone number does not have an email account with Khoj, return the user
    if is_none_or_empty(matched_user.email):
        return matched_user

    # If the user has an email account with Khoj and a verified number, return the user
    if matched_user.verified_phone_number:
        return matched_user

    return None


async def retrieve_user(session_id: str) -> KhojUser:
    session = SessionStore(session_key=session_id)
    if not await sync_to_async(session.exists)(session_key=session_id):
        raise HTTPException(status_code=401, detail="Invalid session")
    session_data = await sync_to_async(session.load)()
    user = await KhojUser.objects.filter(id=session_data.get("_auth_user_id")).afirst()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user")
    return user


def get_all_users() -> BaseManager[KhojUser]:
    return KhojUser.objects.all()


@require_valid_user
def get_user_github_config(user: KhojUser):
    config = GithubConfig.objects.filter(user=user).prefetch_related("githubrepoconfig").first()
    return config


@require_valid_user
def get_user_notion_config(user: KhojUser):
    config = NotionConfig.objects.filter(user=user).first()
    return config


def delete_user_requests(window: timedelta = timedelta(days=1)):
    return UserRequests.objects.filter(created_at__lte=datetime.now(tz=timezone.utc) - window).delete()


@arequire_valid_user
async def aget_user_name(user: KhojUser):
    full_name = user.get_full_name()
    if not is_none_or_empty(full_name):
        return full_name
    google_profile: GoogleUser = await GoogleUser.objects.filter(user=user).afirst()
    if google_profile:
        return google_profile.given_name

    return None


@arequire_valid_user
async def set_user_github_config(user: KhojUser, pat_token: str, repos: list):
    config = await GithubConfig.objects.filter(user=user).afirst()

    if not config:
        config = await GithubConfig.objects.acreate(pat_token=pat_token, user=user)
    else:
        config.pat_token = pat_token
        await config.asave()
        await config.githubrepoconfig.all().adelete()

    for repo in repos:
        await GithubRepoConfig.objects.acreate(
            name=repo["name"], owner=repo["owner"], branch=repo["branch"], github_config=config
        )
    return config


def get_default_search_model() -> SearchModelConfig:
    default_search_model = SearchModelConfig.objects.filter(name="default").first()

    if default_search_model:
        return default_search_model
    elif SearchModelConfig.objects.count() == 0:
        SearchModelConfig.objects.create()
    return SearchModelConfig.objects.first()


def get_or_create_search_models():
    search_models = SearchModelConfig.objects.all()
    if search_models.count() == 0:
        SearchModelConfig.objects.create()
        search_models = SearchModelConfig.objects.all()

    return search_models


class ProcessLockAdapters:
    @staticmethod
    def get_process_lock(process_name: str):
        return ProcessLock.objects.filter(name=process_name).first()

    @staticmethod
    def set_process_lock(process_name: str, max_duration_in_seconds: int = 600):
        return ProcessLock.objects.create(name=process_name, max_duration_in_seconds=max_duration_in_seconds)

    @staticmethod
    def is_process_locked_by_name(process_name: str):
        process_lock = ProcessLock.objects.filter(name=process_name).first()
        if not process_lock:
            return False
        return ProcessLockAdapters.is_process_locked(process_lock)

    @staticmethod
    def is_process_locked(process_lock: ProcessLock):
        if process_lock.started_at + timedelta(seconds=process_lock.max_duration_in_seconds) < datetime.now(
            tz=timezone.utc
        ):
            process_lock.delete()
            logger.info(f"🔓 Deleted stale {process_lock.name} process lock on timeout")
            return False
        return True

    @staticmethod
    def remove_process_lock(process_lock: ProcessLock):
        return process_lock.delete()

    @staticmethod
    def run_with_lock(func: Callable, operation: ProcessLock.Operation, max_duration_in_seconds: int = 600, **kwargs):
        # Exit early if process lock is already taken
        if ProcessLockAdapters.is_process_locked_by_name(operation):
            logger.debug(f"🔒 Skip executing {func} as {operation} lock is already taken")
            return

        success = False
        process_lock = None
        try:
            # Set process lock
            process_lock = ProcessLockAdapters.set_process_lock(operation, max_duration_in_seconds)
            logger.info(f"🔐 Locked {operation} to execute {func}")

            # Execute Function
            with timer(f"🔒 Run {func} with {operation} process lock", logger):
                func(**kwargs)
            success = True
        except IntegrityError as e:
            logger.debug(f"⚠️ Unable to create the process lock for {func} with {operation}: {e}")
            success = False
        except Exception as e:
            logger.error(f"🚨 Error executing {func} with {operation} process lock: {e}", exc_info=True)
            success = False
        finally:
            # Remove Process Lock
            if process_lock:
                ProcessLockAdapters.remove_process_lock(process_lock)
                logger.info(
                    f"🔓 Unlocked {operation} process after executing {func} {'Succeeded' if success else 'Failed'}"
                )
            else:
                logger.debug(f"Skip removing {operation} process lock as it was not set")


@util.close_old_connections
def run_with_process_lock(*args, **kwargs):
    """Wrapper function used for scheduling jobs.
    Required as APScheduler can't discover the `ProcessLockAdapter.run_with_lock' method on its own.
    """
    return ProcessLockAdapters.run_with_lock(*args, **kwargs)


class ClientApplicationAdapters:
    @staticmethod
    async def aget_client_application_by_id(client_id: str, client_secret: str):
        return await ClientApplication.objects.filter(client_id=client_id, client_secret=client_secret).afirst()


class AgentAdapters:
    DEFAULT_AGENT_NAME = "Khoj"
    DEFAULT_AGENT_SLUG = "khoj"

    @staticmethod
    async def aget_readonly_agent_by_slug(agent_slug: str, user: KhojUser):
        return (
            await Agent.objects.filter(
                (Q(slug__iexact=agent_slug.lower()))
                & (
                    Q(privacy_level=Agent.PrivacyLevel.PUBLIC)
                    | Q(privacy_level=Agent.PrivacyLevel.PROTECTED)
                    | Q(creator=user)
                )
            )
            .prefetch_related("creator", "chat_model", "fileobject_set")
            .afirst()
        )

    @staticmethod
    @arequire_valid_user
    async def adelete_agent_by_slug(agent_slug: str, user: KhojUser):
        agent = await AgentAdapters.aget_agent_by_slug(agent_slug, user)
        if agent.creator != user:
            return False

        async for entry in Entry.objects.filter(agent=agent).aiterator():
            await entry.adelete()

        if agent:
            await agent.adelete()
            return True
        return False

    @staticmethod
    async def aget_agent_by_slug(agent_slug: str, user: KhojUser):
        return (
            await Agent.objects.filter(
                (Q(slug__iexact=agent_slug.lower())) & (Q(privacy_level=Agent.PrivacyLevel.PUBLIC) | Q(creator=user))
            )
            .prefetch_related("creator", "chat_model", "fileobject_set")
            .afirst()
        )

    @staticmethod
    async def aget_agent_by_name(agent_name: str, user: KhojUser):
        return (
            await Agent.objects.filter(
                (Q(name__iexact=agent_name.lower())) & (Q(privacy_level=Agent.PrivacyLevel.PUBLIC) | Q(creator=user))
            )
            .prefetch_related("creator", "chat_model", "fileobject_set")
            .afirst()
        )

    @staticmethod
    def get_agent_by_slug(slug: str, user: KhojUser = None):
        if user:
            return Agent.objects.filter(
                (Q(slug__iexact=slug.lower())) & (Q(privacy_level=Agent.PrivacyLevel.PUBLIC) | Q(creator=user))
            ).first()
        return Agent.objects.filter(slug__iexact=slug.lower(), privacy_level=Agent.PrivacyLevel.PUBLIC).first()

    @staticmethod
    def get_all_accessible_agents(user: KhojUser = None):
        public_query = Q(privacy_level=Agent.PrivacyLevel.PUBLIC)
        # TODO Update this to allow any public agent that's officially approved once that experience is launched
        public_query &= Q(managed_by_admin=True)
        if user:
            return (
                Agent.objects.filter(public_query | Q(creator=user))
                .distinct()
                .order_by("created_at")
                .prefetch_related("creator", "chat_model", "fileobject_set")
            )
        return (
            Agent.objects.filter(public_query)
            .order_by("created_at")
            .prefetch_related("creator", "chat_model", "fileobject_set")
        )

    @staticmethod
    async def aget_all_accessible_agents(user: KhojUser = None) -> List[Agent]:
        agents = await sync_to_async(AgentAdapters.get_all_accessible_agents)(user)
        return await sync_to_async(list)(agents)

    @staticmethod
    async def ais_agent_accessible(agent: Agent, user: KhojUser) -> bool:
        agent = await Agent.objects.select_related("creator").aget(pk=agent.pk)

        if agent.privacy_level == Agent.PrivacyLevel.PUBLIC:
            return True
        if agent.creator == user:
            return True
        if agent.privacy_level == Agent.PrivacyLevel.PROTECTED:
            return True
        return False

    @staticmethod
    def get_conversation_agent_by_id(agent_id: int):
        agent = Agent.objects.filter(id=agent_id).first()
        if agent == AgentAdapters.get_default_agent():
            # If the agent is set to the default agent, then return None and let the default application code be used
            return None
        return agent

    @staticmethod
    def get_default_agent():
        return Agent.objects.filter(name=AgentAdapters.DEFAULT_AGENT_NAME).first()

    @staticmethod
    def create_default_agent(user: KhojUser):
        default_chat_model = ConversationAdapters.get_default_chat_model(user)
        if default_chat_model is None:
            logger.info("No default conversation config found, skipping default agent creation")
            return None
        default_personality = prompts.personality.format(current_date="placeholder", day_of_week="placeholder")

        agent = Agent.objects.filter(name=AgentAdapters.DEFAULT_AGENT_NAME).first()

        if agent:
            agent.personality = default_personality
            agent.chat_model = default_chat_model
            agent.slug = AgentAdapters.DEFAULT_AGENT_SLUG
            agent.name = AgentAdapters.DEFAULT_AGENT_NAME
            agent.privacy_level = Agent.PrivacyLevel.PUBLIC
            agent.managed_by_admin = True
            agent.input_tools = []
            agent.output_modes = []
            agent.save()
        else:
            # The default agent is public and managed by the admin. It's handled a little differently than other agents.
            agent = Agent.objects.create(
                name=AgentAdapters.DEFAULT_AGENT_NAME,
                privacy_level=Agent.PrivacyLevel.PUBLIC,
                managed_by_admin=True,
                chat_model=default_chat_model,
                personality=default_personality,
                slug=AgentAdapters.DEFAULT_AGENT_SLUG,
            )
            Conversation.objects.filter(agent=None).update(agent=agent)

        return agent

    @staticmethod
    async def aget_default_agent():
        return await Agent.objects.filter(name=AgentAdapters.DEFAULT_AGENT_NAME).afirst()

    @staticmethod
    @arequire_valid_user
    async def aupdate_agent(
        user: KhojUser,
        name: str,
        personality: str,
        privacy_level: str,
        icon: str,
        color: str,
        chat_model: str,
        files: List[str],
        input_tools: List[str],
        output_modes: List[str],
        slug: Optional[str] = None,
    ):
        chat_model_option = await ChatModel.objects.filter(name=chat_model).afirst()

        # Slug will be None for new agents, which will trigger a new agent creation with a generated, immutable slug
        agent, created = await Agent.objects.filter(slug=slug, creator=user).aupdate_or_create(
            defaults={
                "name": name,
                "creator": user,
                "personality": personality,
                "privacy_level": privacy_level,
                "style_icon": icon,
                "style_color": color,
                "chat_model": chat_model_option,
                "input_tools": input_tools,
                "output_modes": output_modes,
            }
        )

        # Delete all existing files and entries
        await FileObject.objects.filter(agent=agent).adelete()
        await Entry.objects.filter(agent=agent).adelete()

        for file in files:
            reference_file = await FileObject.objects.filter(file_name=file, user=agent.creator).afirst()
            if reference_file:
                await FileObject.objects.acreate(file_name=file, agent=agent, raw_text=reference_file.raw_text)

                # Duplicate all entries associated with the file
                entries: List[Entry] = []
                async for entry in Entry.objects.filter(file_path=file, user=agent.creator).aiterator():
                    entries.append(
                        Entry(
                            agent=agent,
                            embeddings=entry.embeddings,
                            raw=entry.raw,
                            compiled=entry.compiled,
                            heading=entry.heading,
                            file_source=entry.file_source,
                            file_type=entry.file_type,
                            file_path=entry.file_path,
                            file_name=entry.file_name,
                            url=entry.url,
                            hashed_value=entry.hashed_value,
                        )
                    )

                # Bulk create entries
                await Entry.objects.abulk_create(entries)

        return agent


class PublicConversationAdapters:
    @staticmethod
    def get_public_conversation_by_slug(slug: str):
        return PublicConversation.objects.filter(slug=slug).first()

    @staticmethod
    def get_public_conversation_url(public_conversation: PublicConversation):
        # Public conversations are viewable by anyone, but not editable.
        return f"/share/chat/{public_conversation.slug}/"


class ConversationAdapters:
    @staticmethod
    def make_public_conversation_copy(conversation: Conversation):
        return PublicConversation.objects.create(
            source_owner=conversation.user,
            agent=conversation.agent,
            conversation_log=conversation.conversation_log,
            slug=conversation.slug,
            title=conversation.title if conversation.title else conversation.slug,
        )

    @staticmethod
    @require_valid_user
    def get_conversation_by_user(
        user: KhojUser, client_application: ClientApplication = None, conversation_id: str = None
    ) -> Optional[Conversation]:
        if conversation_id:
            conversation = (
                Conversation.objects.filter(user=user, client=client_application, id=conversation_id)
                .order_by("-updated_at")
                .first()
            )
        else:
            agent = AgentAdapters.get_default_agent()
            conversation = (
                Conversation.objects.filter(user=user, client=client_application).order_by("-updated_at").first()
            ) or Conversation.objects.create(user=user, client=client_application, agent=agent)

        return conversation

    @staticmethod
    @require_valid_user
    def get_conversation_sessions(user: KhojUser, client_application: ClientApplication = None):
        return (
            Conversation.objects.filter(user=user, client=client_application)
            .prefetch_related("agent")
            .order_by("-updated_at")
        )

    @staticmethod
    @arequire_valid_user
    async def aset_conversation_title(
        user: KhojUser, client_application: ClientApplication, conversation_id: str, title: str
    ):
        conversation = await Conversation.objects.filter(
            user=user, client=client_application, id=conversation_id
        ).afirst()
        if conversation:
            conversation.title = title
            await conversation.asave()
            return conversation
        return None

    @staticmethod
    def get_conversation_by_id(conversation_id: str):
        return Conversation.objects.filter(id=conversation_id).first()

    @staticmethod
    @arequire_valid_user
    async def acreate_conversation_session(
        user: KhojUser, client_application: ClientApplication = None, agent_slug: str = None, title: str = None
    ):
        if agent_slug:
            agent = await AgentAdapters.aget_readonly_agent_by_slug(agent_slug, user)
            if agent is None:
                raise HTTPException(status_code=400, detail="No such agent currently exists.")
            return await Conversation.objects.select_related("agent", "agent__creator", "agent__chat_model").acreate(
                user=user, client=client_application, agent=agent, title=title
            )
        agent = await AgentAdapters.aget_default_agent()
        return await Conversation.objects.select_related("agent", "agent__creator", "agent__chat_model").acreate(
            user=user, client=client_application, agent=agent, title=title
        )

    @staticmethod
    @require_valid_user
    def create_conversation_session(
        user: KhojUser, client_application: ClientApplication = None, agent_slug: str = None, title: str = None
    ):
        if agent_slug:
            agent = AgentAdapters.aget_readonly_agent_by_slug(agent_slug, user)
            if agent is None:
                raise HTTPException(status_code=400, detail="No such agent currently exists.")
            return Conversation.objects.create(user=user, client=client_application, agent=agent, title=title)
        agent = AgentAdapters.get_default_agent()
        return Conversation.objects.create(user=user, client=client_application, agent=agent, title=title)

    @staticmethod
    @arequire_valid_user
    async def aget_conversation_by_user(
        user: KhojUser,
        client_application: ClientApplication = None,
        conversation_id: str = None,
        title: str = None,
        create_new: bool = False,
    ) -> Optional[Conversation]:
        if create_new:
            return await ConversationAdapters.acreate_conversation_session(user, client_application)

        query = Conversation.objects.filter(user=user, client=client_application).prefetch_related("agent")

        if conversation_id:
            return await query.filter(id=conversation_id).afirst()
        elif title:
            return await query.filter(title=title).afirst()

        conversation = await query.order_by("-updated_at").afirst()

        return conversation or await Conversation.objects.prefetch_related("agent").acreate(
            user=user, client=client_application
        )

    @staticmethod
    @arequire_valid_user
    async def adelete_conversation_by_user(
        user: KhojUser, client_application: ClientApplication = None, conversation_id: str = None
    ):
        if conversation_id:
            return await Conversation.objects.filter(user=user, client=client_application, id=conversation_id).adelete()
        return await Conversation.objects.filter(user=user, client=client_application).adelete()

    @staticmethod
    @require_valid_user
    def has_any_chat_model(user: KhojUser):
        return ChatModel.objects.filter(user=user).exists()

    @staticmethod
    def get_all_chat_models():
        return ChatModel.objects.all()

    @staticmethod
    async def aget_all_chat_models():
        return await sync_to_async(list)(ChatModel.objects.prefetch_related("ai_model_api").all())

    @staticmethod
    def get_vision_enabled_config():
        chat_models = ConversationAdapters.get_all_chat_models()
        for config in chat_models:
            if config.vision_enabled:
                return config
        return None

    @staticmethod
    async def aget_vision_enabled_config():
        chat_models = await ConversationAdapters.aget_all_chat_models()
        for config in chat_models:
            if config.vision_enabled:
                return config
        return None

    @staticmethod
    def get_ai_model_api():
        return AiModelApi.objects.filter().first()

    @staticmethod
    def has_valid_ai_model_api():
        return AiModelApi.objects.filter().exists()

    @staticmethod
    @arequire_valid_user
    async def aset_user_conversation_processor(user: KhojUser, conversation_processor_config_id: int):
        config = await ChatModel.objects.filter(id=conversation_processor_config_id).afirst()
        if not config:
            return None
        new_config = await UserConversationConfig.objects.aupdate_or_create(user=user, defaults={"setting": config})
        return new_config

    @staticmethod
    @arequire_valid_user
    async def aset_user_voice_model(user: KhojUser, model_id: str):
        config = await VoiceModelOption.objects.filter(model_id=model_id).afirst()
        if not config:
            return None
        new_config = await UserVoiceModelConfig.objects.aupdate_or_create(user=user, defaults={"setting": config})
        return new_config

    @staticmethod
    def get_chat_model(user: KhojUser):
        subscribed = is_user_subscribed(user)
        if not subscribed:
            return ConversationAdapters.get_default_chat_model(user)
        config = UserConversationConfig.objects.filter(user=user).first()
        if config:
            return config.setting
        return ConversationAdapters.get_advanced_chat_model(user)

    @staticmethod
    async def aget_chat_model(user: KhojUser):
        subscribed = await ais_user_subscribed(user)
        if not subscribed:
            return await ConversationAdapters.aget_default_chat_model(user)
        config = await UserConversationConfig.objects.filter(user=user).prefetch_related("setting").afirst()
        if config:
            return config.setting
        return ConversationAdapters.aget_advanced_chat_model(user)

    @staticmethod
    async def aget_voice_model_config(user: KhojUser) -> Optional[VoiceModelOption]:
        voice_model_config = await UserVoiceModelConfig.objects.filter(user=user).prefetch_related("setting").afirst()
        if voice_model_config:
            return voice_model_config.setting
        return await VoiceModelOption.objects.afirst()

    @staticmethod
    def get_voice_model_options():
        return VoiceModelOption.objects.all()

    @staticmethod
    def get_voice_model_config(user: KhojUser) -> Optional[VoiceModelOption]:
        voice_model_config = UserVoiceModelConfig.objects.filter(user=user).prefetch_related("setting").first()
        if voice_model_config:
            return voice_model_config.setting
        return VoiceModelOption.objects.first()

    @staticmethod
    def get_default_chat_model(user: KhojUser = None):
        """Get default conversation config. Prefer chat model by server admin > user > first created chat model"""
        # Get the server chat settings
        server_chat_settings = ServerChatSettings.objects.first()

        is_subscribed = is_user_subscribed(user) if user else False
        if server_chat_settings:
            # If the user is subscribed and the advanced model is enabled, return the advanced model
            if is_subscribed and server_chat_settings.chat_advanced:
                return server_chat_settings.chat_advanced
            # If the default model is set, return it
            if server_chat_settings.chat_default:
                return server_chat_settings.chat_default

        # Get the user's chat settings, if the server chat settings are not set
        user_chat_settings = UserConversationConfig.objects.filter(user=user).first() if user else None
        if user_chat_settings is not None and user_chat_settings.setting is not None:
            return user_chat_settings.setting

        # Get the first chat model if even the user chat settings are not set
        return ChatModel.objects.filter().first()

    @staticmethod
    async def aget_default_chat_model(user: KhojUser = None):
        """Get default conversation config. Prefer chat model by server admin > user > first created chat model"""
        # Get the server chat settings
        server_chat_settings: ServerChatSettings = (
            await ServerChatSettings.objects.filter()
            .prefetch_related(
                "chat_default", "chat_default__ai_model_api", "chat_advanced", "chat_advanced__ai_model_api"
            )
            .afirst()
        )
        is_subscribed = await ais_user_subscribed(user) if user else False

        if server_chat_settings:
            # If the user is subscribed and the advanced model is enabled, return the advanced model
            if is_subscribed and server_chat_settings.chat_advanced:
                return server_chat_settings.chat_advanced
            # If the default model is set, return it
            if server_chat_settings.chat_default:
                return server_chat_settings.chat_default

        # Get the user's chat settings, if the server chat settings are not set
        user_chat_settings = (
            (await UserConversationConfig.objects.filter(user=user).prefetch_related("setting__ai_model_api").afirst())
            if user
            else None
        )
        if user_chat_settings is not None and user_chat_settings.setting is not None:
            return user_chat_settings.setting

        # Get the first chat model if even the user chat settings are not set
        return await ChatModel.objects.filter().prefetch_related("ai_model_api").afirst()

    @staticmethod
    def get_advanced_chat_model(user: KhojUser):
        server_chat_settings = ServerChatSettings.objects.first()
        if server_chat_settings is not None and server_chat_settings.chat_advanced is not None:
            return server_chat_settings.chat_advanced
        return ConversationAdapters.get_default_chat_model(user)

    @staticmethod
    async def aget_advanced_chat_model(user: KhojUser = None):
        server_chat_settings: ServerChatSettings = (
            await ServerChatSettings.objects.filter()
            .prefetch_related("chat_advanced", "chat_advanced__ai_model_api")
            .afirst()
        )
        if server_chat_settings is not None and server_chat_settings.chat_advanced is not None:
            return server_chat_settings.chat_advanced
        return await ConversationAdapters.aget_default_chat_model(user)

    @staticmethod
    async def aget_server_webscraper():
        server_chat_settings = await ServerChatSettings.objects.filter().prefetch_related("web_scraper").afirst()
        if server_chat_settings is not None and server_chat_settings.web_scraper is not None:
            return server_chat_settings.web_scraper
        return None

    @staticmethod
    async def aget_enabled_webscrapers() -> list[WebScraper]:
        enabled_scrapers: list[WebScraper] = []
        server_webscraper = await ConversationAdapters.aget_server_webscraper()
        if server_webscraper:
            # Only use the webscraper set in the server chat settings
            enabled_scrapers = [server_webscraper]
        if not enabled_scrapers:
            # Use the enabled web scrapers, ordered by priority, until get web page content
            enabled_scrapers = [scraper async for scraper in WebScraper.objects.all().order_by("priority").aiterator()]
        if not enabled_scrapers:
            # Use scrapers enabled via environment variables
            if os.getenv("FIRECRAWL_API_KEY"):
                api_url = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev")
                enabled_scrapers.append(
                    WebScraper(
                        type=WebScraper.WebScraperType.FIRECRAWL,
                        name=WebScraper.WebScraperType.FIRECRAWL.capitalize(),
                        api_key=os.getenv("FIRECRAWL_API_KEY"),
                        api_url=api_url,
                    )
                )
            if os.getenv("OLOSTEP_API_KEY"):
                api_url = os.getenv("OLOSTEP_API_URL", "https://agent.olostep.com/olostep-p2p-incomingAPI")
                enabled_scrapers.append(
                    WebScraper(
                        type=WebScraper.WebScraperType.OLOSTEP,
                        name=WebScraper.WebScraperType.OLOSTEP.capitalize(),
                        api_key=os.getenv("OLOSTEP_API_KEY"),
                        api_url=api_url,
                    )
                )
            # Jina is the default fallback scrapers to use as it does not require an API key
            api_url = os.getenv("JINA_READER_API_URL", "https://r.jina.ai/")
            enabled_scrapers.append(
                WebScraper(
                    type=WebScraper.WebScraperType.JINA,
                    name=WebScraper.WebScraperType.JINA.capitalize(),
                    api_key=os.getenv("JINA_API_KEY"),
                    api_url=api_url,
                )
            )

            # Only enable the direct web page scraper by default in self-hosted single user setups.
            # Useful for reading webpages on your intranet.
            if state.anonymous_mode or in_debug_mode():
                enabled_scrapers.append(
                    WebScraper(
                        type=WebScraper.WebScraperType.DIRECT,
                        name=WebScraper.WebScraperType.DIRECT.capitalize(),
                        api_key=None,
                        api_url=None,
                    )
                )

        return enabled_scrapers

    @staticmethod
    @require_valid_user
    def create_conversation_from_public_conversation(
        user: KhojUser, public_conversation: PublicConversation, client_app: ClientApplication
    ):
        scrubbed_title = public_conversation.title if public_conversation.title else public_conversation.slug
        if scrubbed_title:
            scrubbed_title = scrubbed_title.replace("-", " ")
        return Conversation.objects.create(
            user=user,
            conversation_log=public_conversation.conversation_log,
            client=client_app,
            slug=scrubbed_title,
            title=public_conversation.title,
            agent=public_conversation.agent,
        )

    @staticmethod
    @require_valid_user
    def save_conversation(
        user: KhojUser,
        conversation_log: dict,
        client_application: ClientApplication = None,
        conversation_id: str = None,
        user_message: str = None,
    ):
        slug = user_message.strip()[:200] if user_message else None
        if conversation_id:
            conversation = Conversation.objects.filter(user=user, client=client_application, id=conversation_id).first()
        else:
            conversation = (
                Conversation.objects.filter(user=user, client=client_application).order_by("-updated_at").first()
            )

        if conversation:
            conversation.conversation_log = conversation_log
            conversation.slug = slug
            conversation.updated_at = datetime.now(tz=timezone.utc)
            conversation.save()
        else:
            Conversation.objects.create(
                user=user, conversation_log=conversation_log, client=client_application, slug=slug
            )

    @staticmethod
    def get_conversation_processor_options():
        return ChatModel.objects.all()

    @staticmethod
    def set_user_chat_model(user: KhojUser, chat_model: ChatModel):
        user_conversation_config, _ = UserConversationConfig.objects.get_or_create(user=user)
        user_conversation_config.setting = chat_model
        user_conversation_config.save()

    @staticmethod
    async def aget_user_chat_model(user: KhojUser):
        config = (
            await UserConversationConfig.objects.filter(user=user).prefetch_related("setting__ai_model_api").afirst()
        )
        if not config:
            return None
        return config.setting

    @staticmethod
    async def get_speech_to_text_config():
        return await SpeechToTextModelOptions.objects.filter().prefetch_related("ai_model_api").afirst()

    @staticmethod
    @arequire_valid_user
    async def aget_conversation_starters(user: KhojUser, max_results=3):
        all_questions = []
        if await ReflectiveQuestion.objects.filter(user=user).aexists():
            all_questions = await sync_to_async(ReflectiveQuestion.objects.filter(user=user).values_list)(
                "question", flat=True
            )

        all_questions = await sync_to_async(ReflectiveQuestion.objects.filter(user=None).values_list)(
            "question", flat=True
        )

        all_questions = await sync_to_async(list)(all_questions)  # type: ignore
        if len(all_questions) < max_results:
            return all_questions

        return random.sample(all_questions, max_results)

    @staticmethod
    def get_valid_chat_model(user: KhojUser, conversation: Conversation):
        agent: Agent = conversation.agent if AgentAdapters.get_default_agent() != conversation.agent else None
        if agent and agent.chat_model:
            chat_model = conversation.agent.chat_model
        else:
            chat_model = ConversationAdapters.get_chat_model(user)

        if chat_model is None:
            chat_model = ConversationAdapters.get_default_chat_model()

        if chat_model.model_type == ChatModel.ModelType.OFFLINE:
            if state.offline_chat_processor_config is None or state.offline_chat_processor_config.loaded_model is None:
                chat_model_name = chat_model.name
                max_tokens = chat_model.max_prompt_size
                state.offline_chat_processor_config = OfflineChatProcessorModel(chat_model_name, max_tokens)

            return chat_model

        if (
            chat_model.model_type
            in [
                ChatModel.ModelType.ANTHROPIC,
                ChatModel.ModelType.OPENAI,
                ChatModel.ModelType.GOOGLE,
            ]
        ) and chat_model.ai_model_api:
            return chat_model

        else:
            raise ValueError("Invalid conversation config - either configure offline chat or openai chat")

    @staticmethod
    async def aget_text_to_image_model_config():
        return await TextToImageModelConfig.objects.filter().prefetch_related("ai_model_api").afirst()

    @staticmethod
    def get_text_to_image_model_config():
        return TextToImageModelConfig.objects.filter().first()

    @staticmethod
    def get_text_to_image_model_options():
        return TextToImageModelConfig.objects.all()

    @staticmethod
    def get_user_text_to_image_model_config(user: KhojUser):
        config = UserTextToImageModelConfig.objects.filter(user=user).first()
        if not config:
            default_config = ConversationAdapters.get_text_to_image_model_config()
            if not default_config:
                return None
            return default_config
        return config.setting

    @staticmethod
    async def aget_user_text_to_image_model(user: KhojUser) -> Optional[TextToImageModelConfig]:
        # Create a custom queryset for prefetching settings__ai_model_api, handling null cases
        settings_prefetch = Prefetch(
            "setting", queryset=TextToImageModelConfig.objects.prefetch_related("ai_model_api")
        )

        config = await UserTextToImageModelConfig.objects.filter(user=user).prefetch_related(settings_prefetch).afirst()
        if not config:
            default_config = await ConversationAdapters.aget_text_to_image_model_config()
            if not default_config:
                return None
            return default_config
        return config.setting

    @staticmethod
    async def aset_user_text_to_image_model(user: KhojUser, text_to_image_model_config_id: int):
        config = await TextToImageModelConfig.objects.filter(id=text_to_image_model_config_id).afirst()
        if not config:
            return None
        new_config, _ = await UserTextToImageModelConfig.objects.aupdate_or_create(
            user=user, defaults={"setting": config}
        )
        return new_config

    @staticmethod
    def add_files_to_filter(user: KhojUser, conversation_id: str, files: List[str]):
        conversation = ConversationAdapters.get_conversation_by_user(user, conversation_id=conversation_id)
        file_list = EntryAdapters.get_all_filenames_by_source(user, "computer")
        if not conversation:
            return []
        for filename in files:
            if filename in file_list and filename not in conversation.file_filters:
                conversation.file_filters.append(filename)
        conversation.save()

        # remove files from conversation.file_filters that are not in file_list
        conversation.file_filters = [file for file in conversation.file_filters if file in file_list]
        conversation.save()
        return conversation.file_filters

    @staticmethod
    def remove_files_from_filter(user: KhojUser, conversation_id: str, files: List[str]):
        conversation = ConversationAdapters.get_conversation_by_user(user, conversation_id=conversation_id)
        if not conversation:
            return []
        for filename in files:
            if filename in conversation.file_filters:
                conversation.file_filters.remove(filename)
        conversation.save()

        # remove files from conversation.file_filters that are not in file_list
        file_list = EntryAdapters.get_all_filenames_by_source(user, "computer")
        conversation.file_filters = [file for file in conversation.file_filters if file in file_list]
        conversation.save()
        return conversation.file_filters

    @staticmethod
    @require_valid_user
    def delete_message_by_turn_id(user: KhojUser, conversation_id: str, turn_id: str):
        conversation = ConversationAdapters.get_conversation_by_user(user, conversation_id=conversation_id)
        if not conversation or not conversation.conversation_log or not conversation.conversation_log.get("chat"):
            return False
        conversation_log = conversation.conversation_log
        updated_log = [msg for msg in conversation_log["chat"] if msg.get("turnId") != turn_id]
        conversation.conversation_log["chat"] = updated_log
        conversation.save()
        return True


class FileObjectAdapters:
    @staticmethod
    def update_raw_text(file_object: FileObject, new_raw_text: str):
        file_object.raw_text = new_raw_text
        file_object.save()

    @staticmethod
    @require_valid_user
    def create_file_object(user: KhojUser, file_name: str, raw_text: str):
        return FileObject.objects.create(user=user, file_name=file_name, raw_text=raw_text)

    @staticmethod
    @require_valid_user
    def get_file_object_by_name(user: KhojUser, file_name: str):
        return FileObject.objects.filter(user=user, file_name=file_name).first()

    @staticmethod
    @require_valid_user
    def get_all_file_objects(user: KhojUser):
        return FileObject.objects.filter(user=user).all()

    @staticmethod
    @require_valid_user
    def delete_file_object_by_name(user: KhojUser, file_name: str):
        return FileObject.objects.filter(user=user, file_name=file_name).delete()

    @staticmethod
    @require_valid_user
    def delete_all_file_objects(user: KhojUser):
        return FileObject.objects.filter(user=user).delete()

    @staticmethod
    async def aupdate_raw_text(file_object: FileObject, new_raw_text: str):
        file_object.raw_text = new_raw_text
        await file_object.asave()

    @staticmethod
    @arequire_valid_user
    async def acreate_file_object(user: KhojUser, file_name: str, raw_text: str):
        return await FileObject.objects.acreate(user=user, file_name=file_name, raw_text=raw_text)

    @staticmethod
    @arequire_valid_user
    async def aget_file_objects_by_name(user: KhojUser, file_name: str, agent: Agent = None):
        return await sync_to_async(list)(FileObject.objects.filter(user=user, file_name=file_name, agent=agent))

    @staticmethod
    @arequire_valid_user
    async def aget_file_objects_by_names(user: KhojUser, file_names: List[str]):
        return await sync_to_async(list)(FileObject.objects.filter(user=user, file_name__in=file_names))

    @staticmethod
    @require_valid_user
    async def aget_all_file_objects(user: KhojUser, start: int = 0, limit: int = 10):
        query = FileObject.objects.filter(user=user).order_by("-updated_at")[start : start + limit]
        return await sync_to_async(list)(query)

    @staticmethod
    @require_valid_user
    async def aget_number_of_pages(user: KhojUser, limit: int = 10):
        count = await FileObject.objects.filter(user=user).acount()
        return math.ceil(count / limit)

    @staticmethod
    @arequire_valid_user
    async def adelete_file_object_by_name(user: KhojUser, file_name: str):
        return await FileObject.objects.filter(user=user, file_name=file_name).adelete()

    @staticmethod
    @arequire_valid_user
    async def adelete_all_file_objects(user: KhojUser):
        return await FileObject.objects.filter(user=user).adelete()


class EntryAdapters:
    word_filter = WordFilter()
    file_filter = FileFilter()
    date_filter = DateFilter()

    @staticmethod
    @require_valid_user
    def does_entry_exist(user: KhojUser, hashed_value: str) -> bool:
        return Entry.objects.filter(user=user, hashed_value=hashed_value).exists()

    @staticmethod
    @require_valid_user
    def delete_entry_by_file(user: KhojUser, file_path: str):
        deleted_count, _ = Entry.objects.filter(user=user, file_path=file_path).delete()
        return deleted_count

    @staticmethod
    @require_valid_user
    def get_filtered_entries(user: KhojUser, file_type: str = None, file_source: str = None):
        queryset = Entry.objects.filter(user=user)

        if file_type is not None:
            queryset = queryset.filter(file_type=file_type)

        if file_source is not None:
            queryset = queryset.filter(file_source=file_source)

        return queryset

    @staticmethod
    @require_valid_user
    def delete_all_entries(user: KhojUser, file_type: str = None, file_source: str = None, batch_size=1000):
        deleted_count = 0
        queryset = EntryAdapters.get_filtered_entries(user, file_type, file_source)
        while queryset.exists():
            batch_ids = list(queryset.values_list("id", flat=True)[:batch_size])
            batch = Entry.objects.filter(id__in=batch_ids, user=user)
            count, _ = batch.delete()
            deleted_count += count
        return deleted_count

    @staticmethod
    @arequire_valid_user
    async def adelete_all_entries(user: KhojUser, file_type: str = None, file_source: str = None, batch_size=1000):
        deleted_count = 0
        queryset = EntryAdapters.get_filtered_entries(user, file_type, file_source)
        while await queryset.aexists():
            batch_ids = await sync_to_async(list)(queryset.values_list("id", flat=True)[:batch_size])
            batch = Entry.objects.filter(id__in=batch_ids, user=user)
            count, _ = await batch.adelete()
            deleted_count += count
        return deleted_count

    @staticmethod
    @require_valid_user
    def get_existing_entry_hashes_by_file(user: KhojUser, file_path: str):
        return Entry.objects.filter(user=user, file_path=file_path).values_list("hashed_value", flat=True)

    @staticmethod
    @require_valid_user
    def delete_entry_by_hash(user: KhojUser, hashed_values: List[str]):
        Entry.objects.filter(user=user, hashed_value__in=hashed_values).delete()

    @staticmethod
    def get_entries_by_date_filter(entry: BaseManager[Entry], start_date: date, end_date: date):
        return entry.filter(
            entrydates__date__gte=start_date,
            entrydates__date__lte=end_date,
        )

    @staticmethod
    @require_valid_user
    def user_has_entries(user: KhojUser):
        return Entry.objects.filter(user=user).exists()

    @staticmethod
    def agent_has_entries(agent: Agent):
        return Entry.objects.filter(agent=agent).exists()

    @staticmethod
    @arequire_valid_user
    async def auser_has_entries(user: KhojUser):
        return await Entry.objects.filter(user=user).aexists()

    @staticmethod
    async def aagent_has_entries(agent: Agent):
        if agent is None:
            return False
        return await Entry.objects.filter(agent=agent).aexists()

    @staticmethod
    @arequire_valid_user
    async def adelete_entry_by_file(user: KhojUser, file_path: str):
        return await Entry.objects.filter(user=user, file_path=file_path).adelete()

    @staticmethod
    @arequire_valid_user
    async def adelete_entries_by_filenames(user: KhojUser, filenames: List[str], batch_size=1000):
        deleted_count = 0
        for i in range(0, len(filenames), batch_size):
            batch = filenames[i : i + batch_size]
            count, _ = await Entry.objects.filter(user=user, file_path__in=batch).adelete()
            deleted_count += count

        return deleted_count

    @staticmethod
    async def aget_agent_entry_filepaths(agent: Agent):
        if agent is None:
            return []
        return await sync_to_async(set)(
            Entry.objects.filter(agent=agent).distinct("file_path").values_list("file_path", flat=True)
        )

    @staticmethod
    @require_valid_user
    def get_all_filenames_by_source(user: KhojUser, file_source: str):
        return (
            Entry.objects.filter(user=user, file_source=file_source)
            .distinct("file_path")
            .values_list("file_path", flat=True)
        )

    @staticmethod
    @require_valid_user
    def get_size_of_indexed_data_in_mb(user: KhojUser):
        entries = Entry.objects.filter(user=user).iterator()
        total_size = sum(sys.getsizeof(entry.compiled) for entry in entries)
        return total_size / 1024 / 1024

    @staticmethod
    def apply_filters(user: KhojUser, query: str, file_type_filter: str = None, agent: Agent = None):
        q_filter_terms = Q()

        word_filters = EntryAdapters.word_filter.get_filter_terms(query)
        file_filters = EntryAdapters.file_filter.get_filter_terms(query)
        date_filters = EntryAdapters.date_filter.get_query_date_range(query)

        owner_filter = Q()

        if user != None:
            owner_filter = Q(user=user)
        if agent != None:
            owner_filter |= Q(agent=agent)

        if owner_filter == Q():
            return Entry.objects.none()

        if len(word_filters) == 0 and len(file_filters) == 0 and len(date_filters) == 0:
            return Entry.objects.filter(owner_filter)

        for term in word_filters:
            if term.startswith("+"):
                q_filter_terms &= Q(raw__icontains=term[1:])
            elif term.startswith("-"):
                q_filter_terms &= ~Q(raw__icontains=term[1:])

        q_file_filter_terms = Q()

        if len(file_filters) > 0:
            for term in file_filters:
                if term.startswith("-"):
                    # Convert the glob term to a regex pattern
                    regex_term = re.escape(term[1:]).replace(r"\*", ".*").replace(r"\?", ".")
                    # Exclude all files that match the regex term
                    q_file_filter_terms &= ~Q(file_path__regex=regex_term)
                else:
                    # Convert the glob term to a regex pattern
                    regex_term = re.escape(term).replace(r"\*", ".*").replace(r"\?", ".")
                    # Include any files that match the regex term
                    q_file_filter_terms |= Q(file_path__regex=regex_term)

            q_filter_terms &= q_file_filter_terms

        if len(date_filters) > 0:
            min_date, max_date = date_filters
            if min_date is not None:
                # Convert the min_date timestamp to yyyy-mm-dd format
                formatted_min_date = date.fromtimestamp(min_date).strftime("%Y-%m-%d")
                q_filter_terms &= Q(embeddings_dates__date__gte=formatted_min_date)
            if max_date is not None:
                # Convert the max_date timestamp to yyyy-mm-dd format
                formatted_max_date = date.fromtimestamp(max_date).strftime("%Y-%m-%d")
                q_filter_terms &= Q(embeddings_dates__date__lte=formatted_max_date)

        relevant_entries = Entry.objects.filter(owner_filter).filter(q_filter_terms)
        if file_type_filter:
            relevant_entries = relevant_entries.filter(file_type=file_type_filter)
        return relevant_entries

    @staticmethod
    def search_with_embeddings(
        raw_query: str,
        embeddings: Tensor,
        user: KhojUser,
        max_results: int = 10,
        file_type_filter: str = None,
        max_distance: float = math.inf,
        agent: Agent = None,
    ):
        owner_filter = Q()

        if user != None:
            owner_filter = Q(user=user)
        if agent != None:
            owner_filter |= Q(agent=agent)

        if owner_filter == Q():
            return Entry.objects.none()

        relevant_entries = EntryAdapters.apply_filters(user, raw_query, file_type_filter, agent)
        relevant_entries = relevant_entries.filter(owner_filter).annotate(
            distance=CosineDistance("embeddings", embeddings)
        )
        relevant_entries = relevant_entries.filter(distance__lte=max_distance)

        if file_type_filter:
            relevant_entries = relevant_entries.filter(file_type=file_type_filter)
        relevant_entries = relevant_entries.order_by("distance")
        return relevant_entries[:max_results]

    @staticmethod
    @require_valid_user
    def get_unique_file_types(user: KhojUser):
        return Entry.objects.filter(user=user).values_list("file_type", flat=True).distinct()

    @staticmethod
    @require_valid_user
    def get_unique_file_sources(user: KhojUser):
        return Entry.objects.filter(user=user).values_list("file_source", flat=True).distinct().all()


class AutomationAdapters:
    @staticmethod
    def get_automations(user: KhojUser) -> Iterable[Job]:
        all_automations: Iterable[Job] = state.scheduler.get_jobs()
        for automation in all_automations:
            if automation.id.startswith(f"automation_{user.uuid}_"):
                yield automation

    @staticmethod
    def get_automation_metadata(user: KhojUser, automation: Job):
        # Perform validation checks
        # Check if user is allowed to delete this automation id
        if not automation.id.startswith(f"automation_{user.uuid}_"):
            raise ValueError("Invalid automation id")

        automation_metadata = json.loads(automation.name)
        crontime = automation_metadata["crontime"]
        timezone = automation.next_run_time.strftime("%Z")
        schedule = f"{cron_descriptor.get_description(crontime)} {timezone}"
        return {
            "id": automation.id,
            "subject": automation_metadata["subject"],
            "query_to_run": automation_metadata["query_to_run"],
            "scheduling_request": automation_metadata["scheduling_request"],
            "schedule": schedule,
            "crontime": crontime,
            "next": automation.next_run_time.strftime("%Y-%m-%d %I:%M %p %Z"),
        }

    @staticmethod
    def get_job_last_run(user: KhojUser, automation: Job):
        # Perform validation checks
        # Check if user is allowed to delete this automation id
        if not automation.id.startswith(f"automation_{user.uuid}_"):
            raise ValueError("Invalid automation id")

        django_job = DjangoJob.objects.filter(id=automation.id).first()
        execution = DjangoJobExecution.objects.filter(job=django_job, status="Executed")

        last_run_time = None

        if execution.exists():
            last_run_time = execution.latest("run_time").run_time

        return last_run_time.strftime("%Y-%m-%d %I:%M %p %Z") if last_run_time else None

    @staticmethod
    def get_automations_metadata(user: KhojUser):
        for automation in AutomationAdapters.get_automations(user):
            yield AutomationAdapters.get_automation_metadata(user, automation)

    @staticmethod
    def get_automation(user: KhojUser, automation_id: str) -> Job:
        # Perform validation checks
        # Check if user is allowed to delete this automation id
        if not automation_id.startswith(f"automation_{user.uuid}_"):
            raise ValueError("Invalid automation id")
        # Check if automation with this id exist
        automation: Job = state.scheduler.get_job(job_id=automation_id)
        if not automation:
            raise ValueError("Invalid automation id")

        return automation

    @staticmethod
    async def aget_automation(user: KhojUser, automation_id: str) -> Job:
        # Perform validation checks
        # Check if user is allowed to delete this automation id
        if not automation_id.startswith(f"automation_{user.uuid}_"):
            raise ValueError("Invalid automation id")
        # Check if automation with this id exist
        automation: Job = await sync_to_async(state.scheduler.get_job)(job_id=automation_id)
        if not automation:
            raise ValueError("Invalid automation id")

        return automation

    @staticmethod
    def delete_automation(user: KhojUser, automation_id: str):
        # Get valid, user-owned automation
        automation: Job = AutomationAdapters.get_automation(user, automation_id)

        # Collate info about user automation to be deleted
        automation_metadata = AutomationAdapters.get_automation_metadata(user, automation)

        automation.remove()
        return automation_metadata
