from django.urls import path

from assistant_context.views import (
    AssistantContextPublishView,
    AssistantContextRevisionListView,
    AssistantContextView,
)


urlpatterns = [
    path("", AssistantContextView.as_view(), name="detail"),
    path("publish/", AssistantContextPublishView.as_view(), name="publish"),
    path("revisions/", AssistantContextRevisionListView.as_view(), name="revision-list"),
]
