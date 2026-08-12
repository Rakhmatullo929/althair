from django.contrib import admin

from sms.models import SMSAuditEvent, SMSConnection, SMSConsent, SMSOutboundAttempt, SMSStatusEvent, SMSWebhookEnvelope


@admin.register(SMSConnection)
class SMSConnectionAdmin(admin.ModelAdmin):
    list_display = ("sender_address", "organization", "provider", "status", "last_health_check_at")
    list_filter = ("provider", "status", "ownership_mode")
    search_fields = ("sender_address", "account_sid", "messaging_service_sid")
    exclude = ("auth_token_encrypted", "api_key_secret_encrypted")
    readonly_fields = ("webhook_public_key", "created_at", "updated_at")


admin.site.register(SMSConsent)
admin.site.register(SMSWebhookEnvelope)
admin.site.register(SMSOutboundAttempt)
admin.site.register(SMSStatusEvent)
admin.site.register(SMSAuditEvent)
