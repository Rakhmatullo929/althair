# Internal control-plane API

All routes are beneath `/api/v1/internal/`, use the dedicated internal cookie, and
return safe errors with request identifiers. Get a CSRF token from `auth/csrf/`, log
in, complete `auth/mfa/verify/`, and send the CSRF header on every unsafe request.

## Resource groups

- `auth/login/`, `auth/mfa/setup/`, `auth/mfa/verify/`, `auth/sessions/`,
  `auth/logout/`, and `me/`: staff session and MFA lifecycle.
- `overview/`: stable stored organization, user, channel, AI, message, Voice, job,
  incident, data-request, and active-control aggregates.
- `organizations/` and `organizations/{id}/`: filtered directory and reasoned,
  redacted inspection. Lifecycle actions are separate reasoned endpoints.
- `controls/`: activate/restore global, tenant, provider, channel, Voice, autopilot,
  and AI-tool controls. Restore requires a control ID and a new reason.
- `providers/` and `providers/{type}/`: redacted connection and infrastructure health.
  Safe actions are health refresh, pause/resume, and circuit-breaker reset.
- `ai/usage/`: runtime configuration, safe run outcomes/errors, handoffs, and token
  totals; never prompts, chain-of-thought, or payloads.
- `jobs/`: safe envelopes. Retry requires the job to be explicitly idempotent;
  queued cancellation and dead-letter acknowledgement are separate transitions.
- `incidents/`: plain-text safe incidents with role-restricted critical resolution.
- `data-requests/`: identity verification, explicit approval/rejection/run, and a
  short-lived export-manifest token. Destructive requests require two distinct owners.
- `entitlements/{organization_id}/`: manual plan/feature/limit overrides; no billing.
- `audit/`: restricted immutable safe summaries.
- `platform-staff/`: owner-only provisioning/mutation and read-only role visibility
  according to policy.
- `settings/`: safe environment/readiness flags only.

Primary lists are paginated or bounded, filtered in the database, and use relation
loading/aggregations rather than per-row queries. Provider tokens, webhook secrets,
customer content, phone data, transcripts, prompts, raw upstream errors, and payloads
are never serialized.
