from django.contrib import admin

from telegram.models import TelegramAuditEvent, TelegramBotConnection, TelegramManagedBotRequest, TelegramManagerEvent, TelegramUserLink, TelegramWebhookEvent


for model in (TelegramUserLink, TelegramManagedBotRequest, TelegramBotConnection, TelegramManagerEvent, TelegramWebhookEvent, TelegramAuditEvent):
    admin.site.register(model)
