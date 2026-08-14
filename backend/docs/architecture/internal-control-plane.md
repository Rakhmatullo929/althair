# Internal control plane architecture

## Boundary

The control plane is a separate administrative security domain. Its Django app is
`control_plane`, its API is mounted only below `/api/v1/internal/`, and its browser
client is the separate `frontend/apps/admin` application. Customer membership,
tenant roles, customer cookies, and Django `is_superuser` never grant control-plane
access. Conversely, a platform role never grants access to an organization-scoped
customer API.

The only shared primitives are stored organization and operational records. Internal
services call explicit, audited control-plane functions; there is no tenant-header
override and no global queryset bypass. The internal organization inspector returns
safe summaries and counts, not customer messages, transcripts, prompts, provider
payloads, credentials, or internal notes.

## Roles

- `platform_owner`: full internal administration, staff lifecycle, and destructive
  data-request approvals. The final active owner cannot be revoked or demoted.
- `platform_admin`: lifecycle, controls, providers, AI, jobs, incidents, and
  entitlements; cannot approve destructive privacy work or administer owners.
- `operations`: provider/job/incident operations without staff or destructive-data
  authority.
- `support`: redacted read-only tenant inspection and incident creation only.
- `security_auditor`: read-only audit/security/data-request visibility.

Every privileged write requires a reason and recent MFA. Role checks are enforced by
the API even when the admin UI hides an action.

## Data domains

`PlatformStaffAccess`, `PlatformMFADevice`, and hashed `PlatformSession` records form
the internal identity domain. `OrganizationOperationalState` and
`OperationalControl` represent lifecycle and emergency state. `PlanCatalog` and
`OrganizationEntitlement` provide manual feature/limit gates without price, payment,
invoice, or subscription collection. `OperationalJob`, `PlatformIncident`, and
`PlatformDataRequest` expose controlled operational workflows. `PlatformAuditEvent`
is append-only at the model and queryset layers.

Existing organizations are assigned the safe `manual` entitlement by migration.
Unknown features fail closed. Entitlements cannot override security, consent, opt-out,
provider, or emergency policy.

## Runtime enforcement

Provider adapters check operational policy immediately before an external send or
call acceptance. AI checks global, organization, autopilot, provider, connection, and
tool controls both before queueing and before execution. Activating a relevant switch
supersedes queued AI work or cancels matching queued operational work; completed
idempotent operations are not replayed. Restore is always a separate audited action.

Customer applications receive only generic restriction and feature-availability
metadata. They never receive the internal actor or reason.

## No impersonation

There is deliberately no “login as customer” feature. Internal staff inspect a
redacted, read-only internal representation under their own platform identity. It
does not mint customer cookies or tokens and every organization inspection requires a
reason and creates an audit event.

## Known limitations

- Billing, prices, invoices, payment collection, and customer subscription
  self-service are not implemented.
- Destructive privacy requests stop at an approved/running worker boundary; a future
  reviewed retention worker must implement final deletion/anonymization.
- Export responses are metadata-only synthetic manifests in this stage. Production
  encrypted object storage is not wired while the control plane is disabled.
- Provider health is an operational aggregation, not a general HTTP or shell console.
- No production deployment, public status page, PostgreSQL RLS, full SIEM, or support
  impersonation is included.
