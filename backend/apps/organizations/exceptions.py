from rest_framework.exceptions import APIException


class TenantAPIException(APIException):
    def __init__(self, detail=None, code=None):
        super().__init__(detail=detail, code=code)
        self.machine_code = code or self.default_code


class MissingOrganizationHeader(TenantAPIException):
    status_code = 400
    default_detail = "X-Organization-ID header is required."
    default_code = "missing_organization_header"


class InvalidOrganizationHeader(TenantAPIException):
    status_code = 400
    default_detail = "X-Organization-ID must be a valid UUID."
    default_code = "invalid_organization_header"


class OrganizationHeaderMismatch(TenantAPIException):
    status_code = 400
    default_detail = "X-Organization-ID does not match the organization route."
    default_code = "organization_header_mismatch"


class OrganizationAccessDenied(TenantAPIException):
    status_code = 403
    default_detail = "Active organization membership is required."
    default_code = "organization_access_denied"


class OrganizationReadOnly(TenantAPIException):
    status_code = 403
    default_detail = "This organization is read-only in its current status."
    default_code = "organization_read_only"
