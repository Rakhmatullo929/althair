from django.urls import path

from channels.views import ChannelConnectionDetailView, ChannelConnectionListCreateView

urlpatterns = [
    path("", ChannelConnectionListCreateView.as_view(), name="channel-connection-list"),
    path("<uuid:connection_id>/", ChannelConnectionDetailView.as_view(), name="channel-connection-detail"),
]
