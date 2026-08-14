from django.contrib import admin

from control_plane.models import (
    OperationalControl,
    OperationalJob,
    OrganizationEntitlement,
    OrganizationOperationalState,
    PlanCatalog,
    PlatformAuditEvent,
    PlatformDataRequest,
    PlatformIncident,
    PlatformMFADevice,
    PlatformSession,
    PlatformStaffAccess,
)


for model in (
    PlatformStaffAccess,
    PlatformMFADevice,
    PlatformSession,
    PlatformAuditEvent,
    OrganizationOperationalState,
    OperationalControl,
    PlanCatalog,
    OrganizationEntitlement,
    OperationalJob,
    PlatformIncident,
    PlatformDataRequest,
):
    admin.site.register(model)
