from django.urls import path

from gmail_integration.views import GmailPubSubView


urlpatterns = [
    path("gmail/pubsub/", GmailPubSubView.as_view(), name="gmail-pubsub"),
    path(
        "google/gmail-pubsub/",
        GmailPubSubView.as_view(),
        name="google-gmail-pubsub",
    ),
]
