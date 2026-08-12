from django.urls import path

from sms.views import (
    SMSConnectionActionView,
    SMSConnectionDetailView,
    SMSConnectionHealthView,
    SMSConnectionListView,
    SMSConsentView,
    SMSPrivacyView,
    SMSReadinessView,
    SMSRetryOutboundView,
    SMSRotateCredentialsView,
    SMSTestView,
)


urlpatterns = [
    path("integrations/sms/readiness/", SMSReadinessView.as_view(), name="sms-readiness"),
    path("integrations/sms/connections/", SMSConnectionListView.as_view(), name="sms-connections"),
    path("integrations/sms/<uuid:connection_id>/", SMSConnectionDetailView.as_view(), name="sms-detail"),
    path("integrations/sms/<uuid:connection_id>/health/", SMSConnectionHealthView.as_view(), name="sms-health"),
    path("integrations/sms/<uuid:connection_id>/test/", SMSTestView.as_view(), name="sms-test"),
    path("integrations/sms/<uuid:connection_id>/retry/", SMSRetryOutboundView.as_view(), name="sms-retry"),
    path("integrations/sms/<uuid:connection_id>/rotate-credentials/", SMSRotateCredentialsView.as_view(), name="sms-rotate"),
    path("integrations/sms/<uuid:connection_id>/privacy/", SMSPrivacyView.as_view(), name="sms-privacy"),
    path("integrations/sms/<uuid:connection_id>/consent/", SMSConsentView.as_view(), name="sms-consent"),
    path("integrations/sms/<uuid:connection_id>/<str:action>/", SMSConnectionActionView.as_view(), name="sms-action"),
]
