from django.urls import path

from instagram.views import (
    InstagramBackfillView,
    InstagramConnectionDetailView,
    InstagramConnectionListView,
    InstagramDisconnectView,
    InstagramHealthView,
    InstagramOAuthCallbackView,
    InstagramOAuthStartView,
    InstagramOperationsView,
    InstagramReconnectView,
    InstagramTestControlView,
    InstagramTestEventView,
)


urlpatterns = [
    path("integrations/instagram/", InstagramConnectionListView.as_view(), name="instagram-connections"),
    path("integrations/instagram/oauth/start/", InstagramOAuthStartView.as_view(), name="instagram-oauth-start"),
    path("integrations/instagram/oauth/callback/", InstagramOAuthCallbackView.as_view(), name="instagram-oauth-callback"),
    path("integrations/instagram/<uuid:connection_id>/", InstagramConnectionDetailView.as_view(), name="instagram-connection"),
    path("integrations/instagram/<uuid:connection_id>/disconnect/", InstagramDisconnectView.as_view(), name="instagram-disconnect"),
    path("integrations/instagram/<uuid:connection_id>/reconnect/", InstagramReconnectView.as_view(), name="instagram-reconnect"),
    path("integrations/instagram/<uuid:connection_id>/health/", InstagramHealthView.as_view(), name="instagram-health"),
    path("integrations/instagram/<uuid:connection_id>/backfill/", InstagramBackfillView.as_view(), name="instagram-backfill"),
    path("integrations/instagram/<uuid:connection_id>/operations/", InstagramOperationsView.as_view(), name="instagram-operations"),
    path("integrations/instagram/<uuid:connection_id>/test-event/", InstagramTestEventView.as_view(), name="instagram-test-event"),
    path("integrations/instagram/<uuid:connection_id>/test-control/", InstagramTestControlView.as_view(), name="instagram-test-control"),
]
