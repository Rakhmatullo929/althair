# Control-plane operations runbook

## Local development

Keep the control plane disabled in the shared default environment. For local testing,
set `CONTROL_PLANE_ENABLE=true`, `CONTROL_PLANE_FAKE_MFA=true`, use deterministic fake
providers, run migrations, export a local-only `CONTROL_PLANE_SEED_PASSWORD`, and seed
synthetic staff/operational examples with:

```bash
python manage.py seed_control_plane_demo
```

Open the admin app on port 3002. The customer client remains on its own origin and
uses its existing authentication. Never copy a customer cookie into the admin app.

## Organization lifecycle

Before suspending, disabling logins/sends/AI, or reactivating, inspect the safe tenant
summary, describe the affected capabilities in the reason, confirm recent MFA, and
create/link an incident when appropriate. Suspension retains data and creates a
read-only customer state. Reactivation restores only the recorded prior eligibility;
it does not reveal, reconnect, or rotate providers.

## Emergency controls

Use the narrowest switch that stops the unsafe operation. Record a specific reason and
optional expiry. Verify the customer sees a generic operational banner and that the
provider/AI adapter rejects immediately before external action. Review cancelled or
superseded queued work. Restore explicitly after validation; do not replay completed
idempotent work.

## Providers and jobs

Health views contain safe state only. Refresh health or reset a documented circuit
breaker; never use the control plane for an arbitrary provider request. Retry only a
failed/dead-letter job marked `idempotent=true`. Non-idempotent retry rejection is a
safety control, not an incident to bypass. Cancel only queued/retrying work and
acknowledge dead letters after investigation.

## Incidents and privacy requests

Incident summaries must be plain text without customer content or credentials.
Critical resolution requires an admin or owner. For privacy work, verify request
identity before approval. Export needs one approval; anonymize/delete needs two
distinct owners. The current destructive workflow intentionally stops at the reviewed
worker boundary. Export links expire and manifests exclude secrets and content.

## Audit review

Review login failure/success, staff changes, organization inspection, lifecycle,
controls, retries/cancellations, entitlements, incidents, and privacy actions. Audit
records are immutable in application code. Export/read access is restricted; raw
message bodies, transcripts, prompts, provider payloads, TOTP, recovery codes, and
credentials must never be added to reasons or summaries.
