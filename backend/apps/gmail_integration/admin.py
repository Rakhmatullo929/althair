from django.contrib import admin

from gmail_integration.models import (
    GmailAuditEvent,
    GmailConnection,
    GmailMessageRecord,
    GmailNotification,
    GmailOAuthState,
    GmailOutboundAttempt,
    GmailSyncRun,
)


for model in (
    GmailConnection,
    GmailOAuthState,
    GmailNotification,
    GmailSyncRun,
    GmailMessageRecord,
    GmailOutboundAttempt,
    GmailAuditEvent,
):
    admin.site.register(model)
