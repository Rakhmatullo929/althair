from django.contrib import admin

from instagram.models import (
    InstagramConnection,
    InstagramConversationWindow,
    InstagramOAuthState,
    InstagramOutboundAttempt,
    InstagramWebhookEvent,
)


for model in (
    InstagramConnection,
    InstagramOAuthState,
    InstagramWebhookEvent,
    InstagramConversationWindow,
    InstagramOutboundAttempt,
):
    admin.site.register(model)
