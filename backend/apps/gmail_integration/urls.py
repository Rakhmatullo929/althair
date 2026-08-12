from django.urls import path

from gmail_integration.views import (
    GmailConnectionDetailView,
    GmailAttachmentView,
    GmailConnectionListView,
    GmailCancelInitialSyncView,
    GmailDisconnectView,
    GmailHealthView,
    GmailOAuthCallbackView,
    GmailOAuthStartView,
    GmailPrivacyView,
    GmailReadinessView,
    GmailReconnectView,
    GmailRenewWatchView,
    GmailResyncView,
    GmailTestInboundView,
    GmailTestStateView,
)


urlpatterns = [
    path("integrations/gmail/readiness/", GmailReadinessView.as_view(), name="gmail-readiness"),
    path("integrations/gmail/", GmailConnectionListView.as_view(), name="gmail-connections"),
    path("integrations/gmail/oauth/start/", GmailOAuthStartView.as_view(), name="gmail-oauth-start"),
    path("integrations/gmail/oauth/callback/", GmailOAuthCallbackView.as_view(), name="gmail-oauth-callback"),
    path("integrations/gmail/<uuid:connection_id>/", GmailConnectionDetailView.as_view(), name="gmail-connection"),
    path("integrations/gmail/<uuid:connection_id>/disconnect/", GmailDisconnectView.as_view(), name="gmail-disconnect"),
    path("integrations/gmail/<uuid:connection_id>/reconnect/", GmailReconnectView.as_view(), name="gmail-reconnect"),
    path("integrations/gmail/<uuid:connection_id>/health/", GmailHealthView.as_view(), name="gmail-health"),
    path("integrations/gmail/<uuid:connection_id>/watch/renew/", GmailRenewWatchView.as_view(), name="gmail-watch-renew"),
    path("integrations/gmail/<uuid:connection_id>/resync/", GmailResyncView.as_view(), name="gmail-resync"),
    path("integrations/gmail/<uuid:connection_id>/sync/cancel/", GmailCancelInitialSyncView.as_view(), name="gmail-sync-cancel"),
    path("integrations/gmail/<uuid:connection_id>/privacy/", GmailPrivacyView.as_view(), name="gmail-privacy"),
    path("integrations/gmail/<uuid:connection_id>/test-inbound/", GmailTestInboundView.as_view(), name="gmail-test-inbound"),
    path("integrations/gmail/<uuid:connection_id>/test-state/", GmailTestStateView.as_view(), name="gmail-test-state"),
    path("integrations/gmail/attachments/<uuid:record_id>/<int:index>/", GmailAttachmentView.as_view(), name="gmail-attachment"),
]
