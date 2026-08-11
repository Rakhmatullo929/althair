from django.urls import path

from telegram.views import TelegramBotWebhookView, TelegramManagerWebhookView

app_name = "telegram_webhooks"

urlpatterns = [
    path("telegram/manager/", TelegramManagerWebhookView.as_view(), name="manager"),
    path("telegram/bots/<str:public_key>/", TelegramBotWebhookView.as_view(), name="bot"),
]
