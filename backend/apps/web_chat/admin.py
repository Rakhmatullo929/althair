from django.contrib import admin

from web_chat.models import WebChatEvent, WebChatInstallation, WebChatKeyRotation, WebChatMetric, WebChatSession


admin.site.register(WebChatInstallation)
admin.site.register(WebChatSession)
admin.site.register(WebChatEvent)
admin.site.register(WebChatKeyRotation)
admin.site.register(WebChatMetric)
