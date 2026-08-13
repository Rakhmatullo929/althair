from django.urls import path

from voice.views import OpenAIRealtimeIncomingCallView, TwilioVoiceStatusView


urlpatterns = [
    path("openai/realtime-calls/", OpenAIRealtimeIncomingCallView.as_view(), name="openai-realtime-calls"),
    path("twilio/voice/<str:public_key>/status/", TwilioVoiceStatusView.as_view(), name="twilio-voice-status"),
]
