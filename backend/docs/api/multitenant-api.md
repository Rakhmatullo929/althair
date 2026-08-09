# Multi-tenant API contract

Authenticated customer routes use `X-Organization-ID: <uuid>` unless noted otherwise.

- `GET /api/v1/me/`
- `GET|POST /api/v1/organizations/`
- `GET|PATCH /api/v1/organizations/{organization_id}/`
- `GET|PATCH /api/v1/organizations/{organization_id}/profile/`
- branch list/create/detail/update/delete below an organization
- membership list/update below an organization
- channel connection list/create/detail/update/delete at `/api/v1/channel-connections/`
- tenant-scoped legacy jobs, knowledge base, and escalation routes
- `POST /api/v1/public/early-access/` (server-to-server public lead intake)
- `GET /health/live` and `GET /health/ready`

Channel credential payloads and webhook secrets are write-only. The early-access route expects the
landing server's `X-Lead-Webhook-Secret`; browsers never receive that value.

For local routing, set `DEV_*_DESTINATION_*` values and run `seed_dev_workspace`. This creates
active non-secret connection records; provider credentials must be supplied through environment or
the write-only channel API, never migrations.
