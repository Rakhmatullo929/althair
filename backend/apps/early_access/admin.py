from django.contrib import admin

from early_access.models import EarlyAccessLead


@admin.register(EarlyAccessLead)
class EarlyAccessLeadAdmin(admin.ModelAdmin):
    list_display = ("company_name", "contact", "industry", "locale", "created_at")
    list_filter = ("industry", "locale", "preferred_channel", "created_at")
    search_fields = ("company_name", "full_name", "contact")
    readonly_fields = ("payload_hash", "created_at")
