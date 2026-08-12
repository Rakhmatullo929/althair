from django.urls import path

from sms.views import TwilioSMSStatusView, TwilioSMSWebhookView


urlpatterns = [
    path("twilio/sms/<str:public_key>/inbound/", TwilioSMSWebhookView.as_view(), name="twilio-sms-inbound"),
    path("twilio/sms/<str:public_key>/status/", TwilioSMSStatusView.as_view(), name="twilio-sms-status"),
]
