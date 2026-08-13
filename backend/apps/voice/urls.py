from django.urls import path

from voice.views import (
    VoiceCallDetailView,
    VoiceCallListView,
    VoiceConnectionActionView,
    VoiceConnectionDetailView,
    VoiceConnectionHealthView,
    VoiceConnectionListView,
    VoiceFakeTestCallView,
    VoiceHumanTakeoverView,
    VoiceReadinessView,
    VoiceRotateCredentialsView,
    VoiceTransferDetailView,
    VoiceTransferListView,
)


urlpatterns = [
    path("integrations/voice/readiness/", VoiceReadinessView.as_view(), name="voice-readiness"),
    path("integrations/voice/connections/", VoiceConnectionListView.as_view(), name="voice-connections"),
    path("integrations/voice/<uuid:connection_id>/", VoiceConnectionDetailView.as_view(), name="voice-connection"),
    path("integrations/voice/<uuid:connection_id>/health/", VoiceConnectionHealthView.as_view(), name="voice-health"),
    path("integrations/voice/<uuid:connection_id>/rotate-credentials/", VoiceRotateCredentialsView.as_view(), name="voice-credentials"),
    path("integrations/voice/<uuid:connection_id>/transfers/", VoiceTransferListView.as_view(), name="voice-transfers"),
    path("integrations/voice/<uuid:connection_id>/transfers/<uuid:destination_id>/", VoiceTransferDetailView.as_view(), name="voice-transfer"),
    path("integrations/voice/<uuid:connection_id>/test-call/", VoiceFakeTestCallView.as_view(), name="voice-test-call"),
    path("integrations/voice/<uuid:connection_id>/<str:action>/", VoiceConnectionActionView.as_view(), name="voice-action"),
    path("voice/calls/", VoiceCallListView.as_view(), name="voice-calls"),
    path("voice/calls/<uuid:call_id>/", VoiceCallDetailView.as_view(), name="voice-call"),
    path("voice/calls/<uuid:call_id>/takeover/", VoiceHumanTakeoverView.as_view(), name="voice-takeover"),
]
