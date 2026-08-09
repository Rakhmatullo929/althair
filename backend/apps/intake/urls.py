from django.urls import path

from intake.views.email import OutlookEmailWebhookView
from intake.views.escalate import EscalationCreateView
from intake.views.escalations import (
    EscalationAcknowledgeAllView,
    EscalationAcknowledgeView,
    EscalationListView,
    EscalationUnreadCountView,
)
from intake.views.knowledge_base import KnowledgeBaseSearchView
from intake.views.lookup import ContextLookupView
from intake.views.sms import TwilioSmsWebhookView
from intake.views.voice import TwilioVoiceWebhookView

urlpatterns = [
    # ── Inbound webhooks (public, AllowAny + throttle + signature) ─────────────
    path(
        'webhooks/twilio/sms/',
        TwilioSmsWebhookView.as_view(),
        name='twilio-sms-webhook',
    ),
    path(
        'webhooks/twilio/voice/',
        TwilioVoiceWebhookView.as_view(),
        name='twilio-voice-webhook',
    ),
    path(
        'webhooks/outlook/email/',
        OutlookEmailWebhookView.as_view(),
        name='outlook-email-webhook',
    ),

    # ── Voice escalation create (external Voice platform, static bearer) ───────
    path(
        'escalate/',
        EscalationCreateView.as_view(),
        name='escalate-create',
    ),

    # ── Escalation queue (authenticated) ───────────────────────────────────────
    path(
        'escalations/',
        EscalationListView.as_view(),
        name='escalation-list',
    ),
    path(
        'escalations/unread-count/',
        EscalationUnreadCountView.as_view(),
        name='escalation-unread-count',
    ),
    path(
        'escalations/acknowledge-all/',
        EscalationAcknowledgeAllView.as_view(),
        name='escalation-acknowledge-all',
    ),
    path(
        'escalations/<uuid:pk>/acknowledge/',
        EscalationAcknowledgeView.as_view(),
        name='escalation-acknowledge',
    ),

    # ── Knowledge Base (authenticated) ────────────────────────────────────────
    path(
        'knowledge-base/',
        KnowledgeBaseSearchView.as_view(),
        name='knowledge-base-search',
    ),

    # ── Context lookup for the external Voice platform (static bearer token) ───
    path(
        'context-lookup/',
        ContextLookupView.as_view(),
        name='context-lookup',
    ),
]
