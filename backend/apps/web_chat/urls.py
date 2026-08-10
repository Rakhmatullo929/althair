from django.urls import path

from web_chat.views import (
    ActivateInstallationView,
    InstallationDetailView,
    InstallationListCreateView,
    InstallationMetricsView,
    InstallationSessionsView,
    PauseInstallationView,
    RevokeInstallationView,
    RotateInstallationKeyView,
    StaffAnonymizeSessionView,
)


urlpatterns = [
    path("web-chat/installations/", InstallationListCreateView.as_view(), name="web-chat-installations"),
    path("web-chat/installations/<uuid:installation_id>/", InstallationDetailView.as_view(), name="web-chat-installation"),
    path("web-chat/installations/<uuid:installation_id>/activate/", ActivateInstallationView.as_view(), name="web-chat-activate"),
    path("web-chat/installations/<uuid:installation_id>/pause/", PauseInstallationView.as_view(), name="web-chat-pause"),
    path("web-chat/installations/<uuid:installation_id>/revoke/", RevokeInstallationView.as_view(), name="web-chat-revoke"),
    path("web-chat/installations/<uuid:installation_id>/rotate-key/", RotateInstallationKeyView.as_view(), name="web-chat-rotate"),
    path("web-chat/installations/<uuid:installation_id>/sessions/", InstallationSessionsView.as_view(), name="web-chat-sessions"),
    path("web-chat/installations/<uuid:installation_id>/metrics/", InstallationMetricsView.as_view(), name="web-chat-metrics"),
    path("web-chat/installations/<uuid:installation_id>/sessions/<uuid:public_session_id>/anonymize/", StaffAnonymizeSessionView.as_view(), name="web-chat-anonymize"),
]
