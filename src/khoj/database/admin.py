import csv
import json
from datetime import datetime, timedelta
from urllib.parse import quote

from apscheduler.job import Job
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.http import HttpResponse
from django_apscheduler.admin import DjangoJobAdmin, DjangoJobExecutionAdmin
from django_apscheduler.jobstores import DjangoJobStore
from django_apscheduler.models import DjangoJob, DjangoJobExecution
from unfold import admin as unfold_admin

from khoj.database.models import (
    Agent,
    AiModelApi,
    ChatModel,
    ClientApplication,
    Conversation,
    Entry,
    GithubConfig,
    KhojUser,
    NotionConfig,
    ProcessLock,
    ReflectiveQuestion,
    SearchModelConfig,
    ServerChatSettings,
    SpeechToTextModelOptions,
    Subscription,
    TextToImageModelConfig,
    UserConversationConfig,
    UserRequests,
    UserVoiceModelConfig,
    VoiceModelOption,
    WebScraper,
)
from khoj.utils.helpers import ImageIntentType


class KhojDjangoJobAdmin(DjangoJobAdmin, unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "next_run_time",
        "job_info",
    )
    search_fields = ("id", "next_run_time")
    ordering = ("-next_run_time",)
    job_store = DjangoJobStore()

    def job_info(self, obj):
        job: Job = self.job_store.lookup_job(obj.id)
        return f"{job.func_ref} {job.args} {job.kwargs}" if job else "None"

    job_info.short_description = "Job Info"  # type: ignore

    def get_search_results(self, request, queryset, search_term):
        queryset, use_distinct = super().get_search_results(request, queryset, search_term)
        if search_term:
            jobs = [job.id for job in self.job_store.get_all_jobs() if search_term in str(job)]
            queryset |= self.model.objects.filter(id__in=jobs)
        return queryset, use_distinct


class KhojDjangoJobExecutionAdmin(DjangoJobExecutionAdmin, unfold_admin.ModelAdmin):
    pass


admin.site.unregister(DjangoJob)
admin.site.register(DjangoJob, KhojDjangoJobAdmin)
admin.site.unregister(DjangoJobExecution)
admin.site.register(DjangoJobExecution, KhojDjangoJobExecutionAdmin)


class GroupAdmin(BaseGroupAdmin, unfold_admin.ModelAdmin):
    pass


class UserAdmin(BaseUserAdmin, unfold_admin.ModelAdmin):
    pass


class KhojUserAdmin(UserAdmin, unfold_admin.ModelAdmin):
    class DateJoinedAfterFilter(admin.SimpleListFilter):
        title = "Joined after"
        parameter_name = "joined_after"

        def lookups(self, request, model_admin):
            return (
                ("1d", "Last 24 hours"),
                ("7d", "Last 7 days"),
                ("30d", "Last 30 days"),
                ("90d", "Last 90 days"),
            )

        def queryset(self, request, queryset):
            if self.value():
                days = int(self.value().rstrip("d"))
                date_threshold = datetime.now() - timedelta(days=days)
                return queryset.filter(date_joined__gte=date_threshold)
            return queryset

    class HasGoogleAuthFilter(admin.SimpleListFilter):
        title = "Has Google Auth"
        parameter_name = "has_google_auth"

        def lookups(self, request, model_admin):
            return (("True", "True"), ("False", "False"))

        def queryset(self, request, queryset):
            if self.value() == "True":
                return queryset.filter(googleuser__isnull=False)
            if self.value() == "False":
                return queryset.filter(googleuser__isnull=True)

    list_display = (
        "id",
        "email",
        "username",
        "phone_number",
        "is_active",
        "uuid",
        "is_staff",
        "is_superuser",
    )
    search_fields = ("email", "username", "phone_number", "uuid")
    filter_horizontal = ("groups", "user_permissions")

    list_filter = (
        HasGoogleAuthFilter,
        DateJoinedAfterFilter,
        "verified_email",
    ) + UserAdmin.list_filter

    fieldsets = (
        (
            "Personal info",
            {
                "fields": (
                    "phone_number",
                    "email_verification_code",
                    "verified_phone_number",
                    "verified_email",
                    "email_verification_code_expiry",
                )
            },
        ),
    ) + UserAdmin.fieldsets

    actions = ["get_email_login_url"]

    def get_email_login_url(self, request, queryset):
        for user in queryset:
            if user.email:
                host = request.get_host()
                otp = quote(user.email_verification_code)
                encoded_email = quote(user.email)
                login_url = f"{host}/auth/magic?code={otp}&email={encoded_email}"
                messages.info(request, f"Email login URL for {user.email}: {login_url}")

    get_email_login_url.short_description = "Get email login URL"  # type: ignore


admin.site.unregister(Group)
admin.site.register(KhojUser, KhojUserAdmin)

admin.site.register(ProcessLock, unfold_admin.ModelAdmin)
admin.site.register(SpeechToTextModelOptions, unfold_admin.ModelAdmin)
admin.site.register(ReflectiveQuestion, unfold_admin.ModelAdmin)
admin.site.register(ClientApplication, unfold_admin.ModelAdmin)
admin.site.register(GithubConfig, unfold_admin.ModelAdmin)
admin.site.register(NotionConfig, unfold_admin.ModelAdmin)
admin.site.register(UserVoiceModelConfig, unfold_admin.ModelAdmin)
admin.site.register(VoiceModelOption, unfold_admin.ModelAdmin)
admin.site.register(UserRequests, unfold_admin.ModelAdmin)


@admin.register(Agent)
class AgentAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "name",
    )
    search_fields = ("id", "name")
    list_filter = ("privacy_level",)
    ordering = ("-created_at",)


@admin.register(Entry)
class EntryAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "created_at",
        "updated_at",
        "user",
        "agent",
        "file_source",
        "file_type",
        "file_name",
        "file_path",
    )
    search_fields = ("id", "user__email", "user__username", "file_path")
    list_filter = (
        "file_type",
        "user__email",
        "search_model__name",
    )
    ordering = ("-created_at",)


@admin.register(Subscription)
class KhojUserSubscription(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "type",
    )

    search_fields = ("id", "user__email", "user__username", "type")
    list_filter = ("type",)


@admin.register(ChatModel)
class ChatModelAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "ai_model_api",
        "max_prompt_size",
    )
    search_fields = ("id", "name", "ai_model_api__name")


@admin.register(TextToImageModelConfig)
class TextToImageModelOptionsAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "model_name",
        "model_type",
    )
    search_fields = ("id", "model_name", "model_type")


@admin.register(AiModelApi)
class AiModelApiAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "api_key",
        "api_base_url",
    )
    search_fields = ("id", "name", "api_key", "api_base_url")


@admin.register(SearchModelConfig)
class SearchModelConfigAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "bi_encoder",
        "cross_encoder",
    )
    search_fields = ("id", "name", "bi_encoder", "cross_encoder")


@admin.register(ServerChatSettings)
class ServerChatSettingsAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "chat_default",
        "chat_advanced",
        "web_scraper",
    )


@admin.register(WebScraper)
class WebScraperAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "priority",
        "name",
        "type",
        "api_key",
        "api_url",
        "created_at",
    )
    search_fields = ("name", "api_key", "api_url", "type")
    ordering = ("priority",)


@admin.register(Conversation)
class ConversationAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "created_at",
        "updated_at",
        "client",
    )
    search_fields = ("id", "user__email", "user__username", "client__name")
    list_filter = ("agent", "client", "user")
    ordering = ("-created_at",)

    actions = ["export_selected_objects", "export_selected_minimal_objects"]

    def export_selected_objects(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="conversations.csv"'

        writer = csv.writer(response)
        writer.writerow(["id", "user", "created_at", "updated_at", "conversation_log"])

        for conversation in queryset:
            modified_log = conversation.conversation_log
            chat_log = modified_log.get("chat", [])
            for idx, log in enumerate(chat_log):
                if log["by"] == "khoj" and log["images"]:
                    log["images"] = ["inline image redacted for space"]
                    chat_log[idx] = log

            modified_log["chat"] = chat_log

            writer.writerow(
                [
                    conversation.id,
                    conversation.user,
                    conversation.created_at,
                    conversation.updated_at,
                    json.dumps(modified_log),
                ]
            )

        return response

    export_selected_objects.short_description = "Export selected conversations"  # type: ignore

    def export_selected_minimal_objects(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="conversations.csv"'

        writer = csv.writer(response)
        writer.writerow(["id", "user", "created_at", "updated_at", "conversation_log"])

        fields_to_keep = set(["message", "by", "created"])

        for conversation in queryset:
            return_log = dict()
            chat_log = conversation.conversation_log.get("chat", [])
            for idx, log in enumerate(chat_log):
                updated_log = {}
                for key in fields_to_keep:
                    updated_log[key] = log[key]
                if (
                    log["by"] == "khoj"
                    and log["intent"]
                    and log["intent"]["type"]
                    and (
                        log["intent"]["type"] == ImageIntentType.TEXT_TO_IMAGE.value
                        or log["intent"]["type"] == ImageIntentType.TEXT_TO_IMAGE_V3.value
                    )
                ):
                    updated_log["message"] = "inline image redacted for space"
                chat_log[idx] = updated_log
            return_log["chat"] = chat_log

            writer.writerow(
                [
                    conversation.id,
                    conversation.user,
                    conversation.created_at,
                    conversation.updated_at,
                    json.dumps(return_log),
                ]
            )

        return response

    export_selected_minimal_objects.short_description = "Export selected conversations (minimal)"  # type: ignore

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            if "export_selected_objects" in actions:
                del actions["export_selected_objects"]
            if "export_selected_minimal_objects" in actions:
                del actions["export_selected_minimal_objects"]
        return actions


@admin.register(UserConversationConfig)
class UserConversationConfigAdmin(unfold_admin.ModelAdmin):
    list_display = (
        "id",
        "get_user_email",
        "get_chat_model",
        "get_subscription_type",
    )
    search_fields = ("id", "user__email", "setting__name", "user__subscription__type")
    ordering = ("-updated_at",)

    def get_user_email(self, obj):
        return obj.user.email

    get_user_email.short_description = "User Email"  # type: ignore
    get_user_email.admin_order_field = "user__email"  # type: ignore

    def get_chat_model(self, obj):
        return obj.setting.name if obj.setting else None

    get_chat_model.short_description = "Chat Model"  # type: ignore
    get_chat_model.admin_order_field = "setting__name"  # type: ignore

    def get_subscription_type(self, obj):
        if hasattr(obj.user, "subscription"):
            return obj.user.subscription.type
        return None

    get_subscription_type.short_description = "Subscription Type"  # type: ignore
    get_subscription_type.admin_order_field = "user__subscription__type"  # type: ignore
