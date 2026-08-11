from django.urls import path

from telegram.views import (
    TelegramAccessSettingsView,
    TelegramConnectionActionView,
    TelegramConnectionDetailView,
    TelegramConnectionHealthView,
    TelegramConnectionListView,
    TelegramExistingBotView,
    TelegramIdentityView,
    TelegramManagedRequestListView,
    TelegramReadinessView,
    TelegramRotateTokenView,
    TelegramTestBotEventView,
    TelegramTestManagerEventView,
)

app_name = "telegram"

urlpatterns = [
    path("integrations/telegram/readiness/", TelegramReadinessView.as_view(), name="readiness"),
    path("integrations/telegram/identity/", TelegramIdentityView.as_view(), name="identity"),
    path("integrations/telegram/managed-requests/", TelegramManagedRequestListView.as_view(), name="managed_requests"),
    path("integrations/telegram/existing-bot/", TelegramExistingBotView.as_view(), name="existing_bot"),
    path("integrations/telegram/", TelegramConnectionListView.as_view(), name="connections"),
    path("integrations/telegram/<uuid:connection_id>/", TelegramConnectionDetailView.as_view(), name="connection"),
    path("integrations/telegram/<uuid:connection_id>/health/", TelegramConnectionHealthView.as_view(), name="health"),
    path("integrations/telegram/<uuid:connection_id>/rotate-token/", TelegramRotateTokenView.as_view(), name="rotate"),
    path("integrations/telegram/<uuid:connection_id>/access-settings/", TelegramAccessSettingsView.as_view(), name="access"),
    path("integrations/telegram/<uuid:connection_id>/test-event/", TelegramTestBotEventView.as_view(), name="test_bot_event"),
    path("integrations/telegram/<uuid:connection_id>/<str:action>/", TelegramConnectionActionView.as_view(), name="action"),
    path("integrations/telegram/test-manager-event/", TelegramTestManagerEventView.as_view(), name="test_manager_event"),
]
