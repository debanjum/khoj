import logging
import os
import re
import uuid
from random import choice
from typing import Dict, List, Optional, Union

from django.contrib.auth.models import AbstractUser
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import pre_save
from django.dispatch import receiver
from pgvector.django import VectorField
from phonenumber_field.modelfields import PhoneNumberField
from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


# Pydantic models for type Chat Message validation
class Context(PydanticBaseModel):
    compiled: str
    file: str


class CodeContextFile(PydanticBaseModel):
    filename: str
    b64_data: str


class CodeContextResult(PydanticBaseModel):
    success: bool
    output_files: List[CodeContextFile]
    std_out: str
    std_err: str
    code_runtime: int


class CodeContextData(PydanticBaseModel):
    code: str
    result: Optional[CodeContextResult] = None


class WebPage(PydanticBaseModel):
    link: str
    query: Optional[str] = None
    snippet: str


class AnswerBox(PydanticBaseModel):
    link: Optional[str] = None
    snippet: Optional[str] = None
    title: str
    snippetHighlighted: Optional[List[str]] = None


class PeopleAlsoAsk(PydanticBaseModel):
    link: Optional[str] = None
    question: Optional[str] = None
    snippet: Optional[str] = None
    title: str


class KnowledgeGraph(PydanticBaseModel):
    attributes: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    descriptionLink: Optional[str] = None
    descriptionSource: Optional[str] = None
    imageUrl: Optional[str] = None
    title: str
    type: Optional[str] = None


class OrganicContext(PydanticBaseModel):
    snippet: str
    title: str
    link: str


class OnlineContext(PydanticBaseModel):
    webpages: Optional[Union[WebPage, List[WebPage]]] = None
    answerBox: Optional[AnswerBox] = None
    peopleAlsoAsk: Optional[List[PeopleAlsoAsk]] = None
    knowledgeGraph: Optional[KnowledgeGraph] = None
    organicContext: Optional[List[OrganicContext]] = None


class Intent(PydanticBaseModel):
    type: str
    query: str
    memory_type: str = Field(alias="memory-type")
    inferred_queries: Optional[List[str]] = Field(default=None, alias="inferred-queries")


class TrainOfThought(PydanticBaseModel):
    type: str
    data: str


class ChatMessage(PydanticBaseModel):
    message: str
    trainOfThought: List[TrainOfThought] = []
    context: List[Context] = []
    onlineContext: Dict[str, OnlineContext] = {}
    codeContext: Dict[str, CodeContextData] = {}
    created: str
    images: Optional[List[str]] = None
    queryFiles: Optional[List[Dict]] = None
    excalidrawDiagram: Optional[List[Dict]] = None
    mermaidjsDiagram: str = None
    by: str
    turnId: Optional[str] = None
    intent: Optional[Intent] = None
    automationId: Optional[str] = None


class DbBaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ClientApplication(DbBaseModel):
    name = models.CharField(max_length=200)
    client_id = models.CharField(max_length=200)
    client_secret = models.CharField(max_length=200)

    def __str__(self):
        return self.name


class KhojUser(AbstractUser):
    uuid = models.UUIDField(models.UUIDField(default=uuid.uuid4, editable=False))
    phone_number = PhoneNumberField(null=True, default=None, blank=True)
    verified_phone_number = models.BooleanField(default=False)
    verified_email = models.BooleanField(default=False)
    email_verification_code = models.CharField(max_length=200, null=True, default=None, blank=True)
    email_verification_code_expiry = models.DateTimeField(null=True, default=None, blank=True)

    def save(self, *args, **kwargs):
        if not self.uuid:
            self.uuid = uuid.uuid4()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username} ({self.uuid})"


class GoogleUser(models.Model):
    user = models.OneToOneField(KhojUser, on_delete=models.CASCADE)
    sub = models.CharField(max_length=200)
    azp = models.CharField(max_length=200)
    email = models.CharField(max_length=200)
    name = models.CharField(max_length=200, null=True, default=None, blank=True)
    given_name = models.CharField(max_length=200, null=True, default=None, blank=True)
    family_name = models.CharField(max_length=200, null=True, default=None, blank=True)
    picture = models.CharField(max_length=200, null=True, default=None)
    locale = models.CharField(max_length=200, null=True, default=None, blank=True)

    def __str__(self):
        return self.name


class KhojApiUser(models.Model):
    """User issued API tokens to authenticate Khoj clients"""

    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)
    token = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=50)
    accessed_at = models.DateTimeField(null=True, default=None)


class Subscription(DbBaseModel):
    class Type(models.TextChoices):
        TRIAL = "trial"
        STANDARD = "standard"

    user = models.OneToOneField(KhojUser, on_delete=models.CASCADE, related_name="subscription")
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.STANDARD)
    is_recurring = models.BooleanField(default=False)
    renewal_date = models.DateTimeField(null=True, default=None, blank=True)
    enabled_trial_at = models.DateTimeField(null=True, default=None, blank=True)


class AiModelApi(DbBaseModel):
    name = models.CharField(max_length=200)
    api_key = models.CharField(max_length=200)
    api_base_url = models.URLField(max_length=200, default=None, blank=True, null=True)

    def __str__(self):
        return self.name


class ChatModel(DbBaseModel):
    class ModelType(models.TextChoices):
        OPENAI = "openai"
        OFFLINE = "offline"
        ANTHROPIC = "anthropic"
        GOOGLE = "google"

    max_prompt_size = models.IntegerField(default=None, null=True, blank=True)
    subscribed_max_prompt_size = models.IntegerField(default=None, null=True, blank=True)
    tokenizer = models.CharField(max_length=200, default=None, null=True, blank=True)
    name = models.CharField(max_length=200, default="bartowski/Meta-Llama-3.1-8B-Instruct-GGUF")
    model_type = models.CharField(max_length=200, choices=ModelType.choices, default=ModelType.OFFLINE)
    vision_enabled = models.BooleanField(default=False)
    ai_model_api = models.ForeignKey(AiModelApi, on_delete=models.CASCADE, default=None, null=True, blank=True)

    def __str__(self):
        return self.name


class VoiceModelOption(DbBaseModel):
    model_id = models.CharField(max_length=200)
    name = models.CharField(max_length=200)


class Agent(DbBaseModel):
    class StyleColorTypes(models.TextChoices):
        BLUE = "blue"
        GREEN = "green"
        RED = "red"
        YELLOW = "yellow"
        ORANGE = "orange"
        PURPLE = "purple"
        PINK = "pink"
        TEAL = "teal"
        CYAN = "cyan"
        LIME = "lime"
        INDIGO = "indigo"
        FUCHSIA = "fuchsia"
        ROSE = "rose"
        SKY = "sky"
        AMBER = "amber"
        EMERALD = "emerald"

    class StyleIconTypes(models.TextChoices):
        LIGHTBULB = "Lightbulb"
        HEALTH = "Health"
        ROBOT = "Robot"
        APERTURE = "Aperture"
        GRADUATION_CAP = "GraduationCap"
        JEEP = "Jeep"
        ISLAND = "Island"
        MATH_OPERATIONS = "MathOperations"
        ASCLEPIUS = "Asclepius"
        COUCH = "Couch"
        CODE = "Code"
        ATOM = "Atom"
        CLOCK_COUNTER_CLOCKWISE = "ClockCounterClockwise"
        PENCIL_LINE = "PencilLine"
        CHALKBOARD = "Chalkboard"
        CIGARETTE = "Cigarette"
        CRANE_TOWER = "CraneTower"
        HEART = "Heart"
        LEAF = "Leaf"
        NEWSPAPER_CLIPPING = "NewspaperClipping"
        ORANGE_SLICE = "OrangeSlice"
        SMILEY_MELTING = "SmileyMelting"
        YIN_YANG = "YinYang"
        SNEAKER_MOVE = "SneakerMove"
        STUDENT = "Student"
        OVEN = "Oven"
        GAVEL = "Gavel"
        BROADCAST = "Broadcast"

    class PrivacyLevel(models.TextChoices):
        PUBLIC = "public"
        PRIVATE = "private"
        PROTECTED = "protected"

    class InputToolOptions(models.TextChoices):
        # These map to various ConversationCommand types
        GENERAL = "general"
        ONLINE = "online"
        NOTES = "notes"
        SUMMARIZE = "summarize"
        WEBPAGE = "webpage"

    class OutputModeOptions(models.TextChoices):
        # These map to various ConversationCommand types
        TEXT = "text"
        IMAGE = "image"
        AUTOMATION = "automation"

    creator = models.ForeignKey(
        KhojUser, on_delete=models.CASCADE, default=None, null=True, blank=True
    )  # Creator will only be null when the agents are managed by admin
    name = models.CharField(max_length=200)
    personality = models.TextField()
    input_tools = ArrayField(
        models.CharField(max_length=200, choices=InputToolOptions.choices), default=list, null=True, blank=True
    )
    output_modes = ArrayField(
        models.CharField(max_length=200, choices=OutputModeOptions.choices), default=list, null=True, blank=True
    )
    managed_by_admin = models.BooleanField(default=False)
    chat_model = models.ForeignKey(ChatModel, on_delete=models.CASCADE)
    slug = models.CharField(max_length=200, unique=True)
    style_color = models.CharField(max_length=200, choices=StyleColorTypes.choices, default=StyleColorTypes.BLUE)
    style_icon = models.CharField(max_length=200, choices=StyleIconTypes.choices, default=StyleIconTypes.LIGHTBULB)
    privacy_level = models.CharField(max_length=30, choices=PrivacyLevel.choices, default=PrivacyLevel.PRIVATE)

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        if self.creator is None:
            self.managed_by_admin = True

        if is_new:
            random_sequence = "".join(choice("0123456789") for i in range(6))
            slug = f"{self.name.lower().replace(' ', '-')}-{random_sequence}"
            self.slug = slug

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ProcessLock(DbBaseModel):
    class Operation(models.TextChoices):
        INDEX_CONTENT = "index_content"
        SCHEDULED_JOB = "scheduled_job"
        SCHEDULE_LEADER = "schedule_leader"
        APPLY_MIGRATIONS = "apply_migrations"

    # We need to make sure that some operations are thread-safe. To do so, add locks for potentially shared operations.
    # For example, we need to make sure that only one process is updating the embeddings at a time.
    name = models.CharField(max_length=200, choices=Operation.choices, unique=True)
    started_at = models.DateTimeField(auto_now_add=True)
    max_duration_in_seconds = models.IntegerField(default=60 * 60 * 12)  # 12 hours


@receiver(pre_save, sender=Agent)
def verify_agent(sender, instance, **kwargs):
    # check if this is a new instance
    if instance._state.adding:
        if Agent.objects.filter(name=instance.name, privacy_level=Agent.PrivacyLevel.PUBLIC).exists():
            raise ValidationError(f"A public Agent with the name {instance.name} already exists.")
        if Agent.objects.filter(name=instance.name, creator=instance.creator).exists():
            raise ValidationError(f"A private Agent with the name {instance.name} already exists.")


class NotionConfig(DbBaseModel):
    token = models.CharField(max_length=200)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)


class GithubConfig(DbBaseModel):
    pat_token = models.CharField(max_length=200)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)


class GithubRepoConfig(DbBaseModel):
    name = models.CharField(max_length=200)
    owner = models.CharField(max_length=200)
    branch = models.CharField(max_length=200)
    github_config = models.ForeignKey(GithubConfig, on_delete=models.CASCADE, related_name="githubrepoconfig")


class WebScraper(DbBaseModel):
    class WebScraperType(models.TextChoices):
        FIRECRAWL = "Firecrawl"
        OLOSTEP = "Olostep"
        JINA = "Jina"
        DIRECT = "Direct"

    name = models.CharField(
        max_length=200,
        default=None,
        null=True,
        blank=True,
        unique=True,
        help_text="Friendly name. If not set, it will be set to the type of the scraper.",
    )
    type = models.CharField(max_length=20, choices=WebScraperType.choices, default=WebScraperType.JINA)
    api_key = models.CharField(
        max_length=200,
        default=None,
        null=True,
        blank=True,
        help_text="API key of the web scraper. Only set if scraper service requires an API key. Default is set from env var.",
    )
    api_url = models.URLField(
        max_length=200,
        default=None,
        null=True,
        blank=True,
        help_text="API URL of the web scraper. Only set if scraper service on non-default URL.",
    )
    priority = models.IntegerField(
        default=None,
        null=True,
        blank=True,
        unique=True,
        help_text="Priority of the web scraper. Lower numbers run first.",
    )

    def clean(self):
        error = {}
        if self.name is None:
            self.name = self.type.capitalize()
        if self.api_url is None:
            if self.type == self.WebScraperType.FIRECRAWL:
                self.api_url = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev")
            elif self.type == self.WebScraperType.OLOSTEP:
                self.api_url = os.getenv("OLOSTEP_API_URL", "https://agent.olostep.com/olostep-p2p-incomingAPI")
            elif self.type == self.WebScraperType.JINA:
                self.api_url = os.getenv("JINA_READER_API_URL", "https://r.jina.ai/")
        if self.api_key is None:
            if self.type == self.WebScraperType.FIRECRAWL:
                self.api_key = os.getenv("FIRECRAWL_API_KEY")
                if not self.api_key and self.api_url == "https://api.firecrawl.dev":
                    error["api_key"] = "Set API key to use default Firecrawl. Get API key from https://firecrawl.dev."
            elif self.type == self.WebScraperType.OLOSTEP:
                self.api_key = os.getenv("OLOSTEP_API_KEY")
                if self.api_key is None:
                    error["api_key"] = "Set API key to use Olostep. Get API key from https://olostep.com/."
            elif self.type == self.WebScraperType.JINA:
                self.api_key = os.getenv("JINA_API_KEY")
        if error:
            raise ValidationError(error)

    def save(self, *args, **kwargs):
        self.clean()

        if self.priority is None:
            max_priority = WebScraper.objects.aggregate(models.Max("priority"))["priority__max"]
            self.priority = max_priority + 1 if max_priority else 1

        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ServerChatSettings(DbBaseModel):
    chat_default = models.ForeignKey(
        ChatModel, on_delete=models.CASCADE, default=None, null=True, blank=True, related_name="chat_default"
    )
    chat_advanced = models.ForeignKey(
        ChatModel, on_delete=models.CASCADE, default=None, null=True, blank=True, related_name="chat_advanced"
    )
    web_scraper = models.ForeignKey(
        WebScraper, on_delete=models.CASCADE, default=None, null=True, blank=True, related_name="web_scraper"
    )


class LocalOrgConfig(DbBaseModel):
    input_files = models.JSONField(default=list, null=True)
    input_filter = models.JSONField(default=list, null=True)
    index_heading_entries = models.BooleanField(default=False)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)


class LocalMarkdownConfig(DbBaseModel):
    input_files = models.JSONField(default=list, null=True)
    input_filter = models.JSONField(default=list, null=True)
    index_heading_entries = models.BooleanField(default=False)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)


class LocalPdfConfig(DbBaseModel):
    input_files = models.JSONField(default=list, null=True)
    input_filter = models.JSONField(default=list, null=True)
    index_heading_entries = models.BooleanField(default=False)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)


class LocalPlaintextConfig(DbBaseModel):
    input_files = models.JSONField(default=list, null=True)
    input_filter = models.JSONField(default=list, null=True)
    index_heading_entries = models.BooleanField(default=False)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)


class SearchModelConfig(DbBaseModel):
    class ModelType(models.TextChoices):
        TEXT = "text"

    class ApiType(models.TextChoices):
        HUGGINGFACE = "huggingface"
        OPENAI = "openai"
        LOCAL = "local"

    # This is the model name exposed to users on their settings page
    name = models.CharField(max_length=200, default="default")
    # Type of content the model can generate embeddings for
    model_type = models.CharField(max_length=200, choices=ModelType.choices, default=ModelType.TEXT)
    # Bi-encoder model of sentence-transformer type to load from HuggingFace
    bi_encoder = models.CharField(max_length=200, default="thenlper/gte-small")
    # Config passed to the sentence-transformer model constructor. E.g. device="cuda:0", trust_remote_server=True etc.
    bi_encoder_model_config = models.JSONField(default=dict, blank=True)
    # Query encode configs like prompt, precision, normalize_embeddings, etc. for sentence-transformer models
    bi_encoder_query_encode_config = models.JSONField(default=dict, blank=True)
    # Docs encode configs like prompt, precision, normalize_embeddings, etc. for sentence-transformer models
    bi_encoder_docs_encode_config = models.JSONField(default=dict, blank=True)
    # Cross-encoder model of sentence-transformer type to load from HuggingFace
    cross_encoder = models.CharField(max_length=200, default="mixedbread-ai/mxbai-rerank-xsmall-v1")
    # Config passed to the cross-encoder model constructor. E.g. device="cuda:0", trust_remote_server=True etc.
    cross_encoder_model_config = models.JSONField(default=dict, blank=True)
    # Inference server API endpoint to use for embeddings inference. Bi-encoder model should be hosted on this server
    embeddings_inference_endpoint = models.CharField(max_length=200, default=None, null=True, blank=True)
    # Inference server API Key to use for embeddings inference. Bi-encoder model should be hosted on this server
    embeddings_inference_endpoint_api_key = models.CharField(max_length=200, default=None, null=True, blank=True)
    # Inference server API type to use for embeddings inference.
    embeddings_inference_endpoint_type = models.CharField(
        max_length=200, choices=ApiType.choices, default=ApiType.LOCAL
    )
    # Inference server API endpoint to use for embeddings inference. Cross-encoder model should be hosted on this server
    cross_encoder_inference_endpoint = models.CharField(max_length=200, default=None, null=True, blank=True)
    # Inference server API Key to use for embeddings inference. Cross-encoder model should be hosted on this server
    cross_encoder_inference_endpoint_api_key = models.CharField(max_length=200, default=None, null=True, blank=True)
    # The confidence threshold of the bi_encoder model to consider the embeddings as relevant
    bi_encoder_confidence_threshold = models.FloatField(default=0.18)

    def __str__(self):
        return self.name


class TextToImageModelConfig(DbBaseModel):
    class ModelType(models.TextChoices):
        OPENAI = "openai"
        STABILITYAI = "stability-ai"
        REPLICATE = "replicate"

    model_name = models.CharField(max_length=200, default="dall-e-3")
    model_type = models.CharField(max_length=200, choices=ModelType.choices, default=ModelType.OPENAI)
    api_key = models.CharField(max_length=200, default=None, null=True, blank=True)
    ai_model_api = models.ForeignKey(AiModelApi, on_delete=models.CASCADE, default=None, null=True, blank=True)

    def clean(self):
        # Custom validation logic
        error = {}
        if self.model_type == self.ModelType.OPENAI:
            if self.api_key and self.ai_model_api:
                error[
                    "api_key"
                ] = "Both API key and AI Model API cannot be set for OpenAI models. Please set only one of them."
                error[
                    "ai_model_api"
                ] = "Both API key and OpenAI config cannot be set for OpenAI models. Please set only one of them."
        if self.model_type != self.ModelType.OPENAI:
            if not self.api_key:
                error["api_key"] = "The API key field must be set for non OpenAI models."
            if self.ai_model_api:
                error["ai_model_api"] = "AI Model API cannot be set for non OpenAI models."
        if error:
            raise ValidationError(error)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.model_name} - {self.model_type}"


class SpeechToTextModelOptions(DbBaseModel):
    class ModelType(models.TextChoices):
        OPENAI = "openai"
        OFFLINE = "offline"

    model_name = models.CharField(max_length=200, default="base")
    model_type = models.CharField(max_length=200, choices=ModelType.choices, default=ModelType.OFFLINE)
    ai_model_api = models.ForeignKey(AiModelApi, on_delete=models.CASCADE, default=None, null=True, blank=True)

    def __str__(self):
        return f"{self.model_name} - {self.model_type}"


class UserConversationConfig(DbBaseModel):
    user = models.OneToOneField(KhojUser, on_delete=models.CASCADE)
    setting = models.ForeignKey(ChatModel, on_delete=models.CASCADE, default=None, null=True, blank=True)


class UserVoiceModelConfig(DbBaseModel):
    user = models.OneToOneField(KhojUser, on_delete=models.CASCADE)
    setting = models.ForeignKey(VoiceModelOption, on_delete=models.CASCADE, default=None, null=True, blank=True)


class UserTextToImageModelConfig(DbBaseModel):
    user = models.OneToOneField(KhojUser, on_delete=models.CASCADE)
    setting = models.ForeignKey(TextToImageModelConfig, on_delete=models.CASCADE)


class Conversation(DbBaseModel):
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)
    conversation_log = models.JSONField(default=dict)
    client = models.ForeignKey(ClientApplication, on_delete=models.CASCADE, default=None, null=True, blank=True)

    # Slug is an app-generated conversation identifier. Need not be unique. Used as display title essentially.
    slug = models.CharField(max_length=200, default=None, null=True, blank=True)

    # The title field is explicitly set by the user.
    title = models.CharField(max_length=500, default=None, null=True, blank=True)
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, default=None, null=True, blank=True)
    file_filters = models.JSONField(default=list)
    id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, primary_key=True, db_index=True)

    def clean(self):
        # Validate conversation_log structure
        try:
            messages = self.conversation_log.get("chat", [])
            for msg in messages:
                ChatMessage.model_validate(msg)
        except Exception as e:
            raise ValidationError(f"Invalid conversation_log format: {str(e)}")

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    @property
    def messages(self) -> List[ChatMessage]:
        """Type-hinted accessor for conversation messages"""
        validated_messages = []
        for msg in self.conversation_log.get("chat", []):
            try:
                # Clean up inferred queries if they contain None
                if msg.get("intent") and msg["intent"].get("inferred-queries"):
                    msg["intent"]["inferred-queries"] = [
                        q for q in msg["intent"]["inferred-queries"] if q is not None and isinstance(q, str)
                    ]
                msg["message"] = str(msg.get("message", ""))
                validated_messages.append(ChatMessage.model_validate(msg))
            except ValidationError as e:
                logger.warning(f"Skipping invalid message in conversation: {e}")
                continue
        return validated_messages


class PublicConversation(DbBaseModel):
    source_owner = models.ForeignKey(KhojUser, on_delete=models.CASCADE)
    conversation_log = models.JSONField(default=dict)
    slug = models.CharField(max_length=200, default=None, null=True, blank=True)
    title = models.CharField(max_length=200, default=None, null=True, blank=True)
    agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, default=None, null=True, blank=True)


@receiver(pre_save, sender=PublicConversation)
def verify_public_conversation(sender, instance, **kwargs):
    def generate_random_alphanumeric(length):
        characters = "0123456789abcdefghijklmnopqrstuvwxyz"
        return "".join(choice(characters) for _ in range(length))

    # check if this is a new instance
    if instance._state.adding:
        slug = re.sub(r"\W+", "-", instance.slug.lower())[:50] if instance.slug else uuid.uuid4().hex
        observed_random_id = set()
        while PublicConversation.objects.filter(slug=slug).exists():
            try:
                random_id = generate_random_alphanumeric(7)
            except IndexError:
                raise ValidationError(
                    "Unable to generate a unique slug for the Public Conversation. Please try again later."
                )
            observed_random_id.add(random_id)
            slug = f"{slug}-{random_id}"
        instance.slug = slug


class ReflectiveQuestion(DbBaseModel):
    question = models.CharField(max_length=500)
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE, default=None, null=True, blank=True)


class FileObject(DbBaseModel):
    # Contains the full text of a file that has associated Entry objects
    file_name = models.CharField(max_length=400, default=None, null=True, blank=True)
    raw_text = models.TextField()
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE, default=None, null=True, blank=True)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, default=None, null=True, blank=True)


class Entry(DbBaseModel):
    class EntryType(models.TextChoices):
        IMAGE = "image"
        PDF = "pdf"
        PLAINTEXT = "plaintext"
        MARKDOWN = "markdown"
        ORG = "org"
        NOTION = "notion"
        GITHUB = "github"
        CONVERSATION = "conversation"
        DOCX = "docx"

    class EntrySource(models.TextChoices):
        COMPUTER = "computer"
        NOTION = "notion"
        GITHUB = "github"

    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE, default=None, null=True, blank=True)
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, default=None, null=True, blank=True)
    embeddings = VectorField(dimensions=None)
    raw = models.TextField()
    compiled = models.TextField()
    heading = models.CharField(max_length=1000, default=None, null=True, blank=True)
    file_source = models.CharField(max_length=30, choices=EntrySource.choices, default=EntrySource.COMPUTER)
    file_type = models.CharField(max_length=30, choices=EntryType.choices, default=EntryType.PLAINTEXT)
    file_path = models.CharField(max_length=400, default=None, null=True, blank=True)
    file_name = models.CharField(max_length=400, default=None, null=True, blank=True)
    url = models.URLField(max_length=400, default=None, null=True, blank=True)
    hashed_value = models.CharField(max_length=100)
    corpus_id = models.UUIDField(default=uuid.uuid4, editable=False)
    search_model = models.ForeignKey(SearchModelConfig, on_delete=models.SET_NULL, default=None, null=True, blank=True)
    file_object = models.ForeignKey(FileObject, on_delete=models.CASCADE, default=None, null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.user and self.agent:
            raise ValidationError("An Entry cannot be associated with both a user and an agent.")


class EntryDates(DbBaseModel):
    date = models.DateField()
    entry = models.ForeignKey(Entry, on_delete=models.CASCADE, related_name="embeddings_dates")

    class Meta:
        indexes = [
            models.Index(fields=["date"]),
        ]


class UserRequests(DbBaseModel):
    user = models.ForeignKey(KhojUser, on_delete=models.CASCADE)
    slug = models.CharField(max_length=200)


class DataStore(DbBaseModel):
    key = models.CharField(max_length=200, unique=True)
    value = models.JSONField(default=dict)
    private = models.BooleanField(default=False)
    owner = models.ForeignKey(KhojUser, on_delete=models.CASCADE, default=None, null=True, blank=True)
