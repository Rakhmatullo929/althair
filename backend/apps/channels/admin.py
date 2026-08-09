from django.contrib import admin

from channels.models import ChannelConnection


@admin.register(ChannelConnection)
class ChannelConnectionAdmin(admin.ModelAdmin):
    list_display = ("display_name", "organization", "type", "provider", "status", "last_synced_at")
    list_filter = ("type", "provider", "status")
    search_fields = ("display_name", "external_identifier", "organization__name")
    exclude = ("encrypted_credentials", "webhook_secret_hash")
