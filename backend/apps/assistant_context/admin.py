from django.contrib import admin

from assistant_context.models import AssistantContextRevision, OrganizationAssistantProfile


@admin.register(OrganizationAssistantProfile)
class OrganizationAssistantProfileAdmin(admin.ModelAdmin):
    list_display = ("organization", "status", "version", "published_at", "updated_at")
    readonly_fields = ("published_snapshot", "published_at", "version")


@admin.register(AssistantContextRevision)
class AssistantContextRevisionAdmin(admin.ModelAdmin):
    list_display = ("organization", "version", "published_by", "published_at")
    readonly_fields = ("organization", "profile", "version", "snapshot", "published_by", "published_at")
