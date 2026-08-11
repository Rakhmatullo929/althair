from django.urls import path

from instagram.views import InstagramWebhookView


urlpatterns = [
    path("instagram/", InstagramWebhookView.as_view(), name="instagram-webhook"),
]
