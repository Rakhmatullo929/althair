from django.urls import path

from web_chat.views import (
    PublicCloseView,
    PublicConfigView,
    PublicCreateSessionView,
    PublicEventsView,
    PublicHandoffView,
    PublicIdentityView,
    PublicMessagesView,
    PublicReadView,
    PublicResumeView,
)


urlpatterns = [
    path("installations/<str:public_key>/config/", PublicConfigView.as_view(), name="public-web-chat-config"),
    path("installations/<str:public_key>/sessions/", PublicCreateSessionView.as_view(), name="public-web-chat-session-create"),
    path("sessions/<uuid:public_session_id>/messages/", PublicMessagesView.as_view(), name="public-web-chat-messages"),
    path("sessions/<uuid:public_session_id>/events/", PublicEventsView.as_view(), name="public-web-chat-events"),
    path("sessions/<uuid:public_session_id>/identity/", PublicIdentityView.as_view(), name="public-web-chat-identity"),
    path("sessions/<uuid:public_session_id>/handoff/", PublicHandoffView.as_view(), name="public-web-chat-handoff"),
    path("sessions/<uuid:public_session_id>/read/", PublicReadView.as_view(), name="public-web-chat-read"),
    path("sessions/<uuid:public_session_id>/close/", PublicCloseView.as_view(), name="public-web-chat-close"),
    path("sessions/<uuid:public_session_id>/resume/", PublicResumeView.as_view(), name="public-web-chat-resume"),
]
