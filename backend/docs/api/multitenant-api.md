# Client and multi-tenant API contract

All URLs below are relative to `/api/v1/`. Customer tenant routes require an authenticated cookie
and `X-Organization-ID: <uuid>`. Authentication and public routes must not receive that header.
Unsafe cookie-authenticated requests require the `csrftoken` value from `users/auth/csrf/` in
`X-CSRFToken`.

## Authentication and user context

| Method | Route | Scope |
| --- | --- | --- |
| `GET` | `users/auth/csrf/` | Public CSRF bootstrap |
| `POST` | `users/auth/register/` | Public; atomically creates user, organization, profile, AI Context root, and owner membership |
| `POST` | `users/auth/login/` | Public + CSRF; sets HttpOnly access/refresh cookies |
| `POST` | `users/auth/refresh/` | Cookie + CSRF; rotates access cookie |
| `POST` | `users/auth/logout/` | Cookie + CSRF; blacklists refresh and clears cookies |
| `GET` | `users/auth/me/` | Authenticated identity only; no legacy role authority |
| `GET` | `me/` | Authenticated identity plus active organization memberships |
| `POST` | `users/auth/change-password/` | Authenticated + CSRF |
| `POST` | `users/auth/invitations/inspect/` | Public; token is in the body, never the URL sent to the API |
| `POST` | `users/auth/invitations/accept/` | Public or authenticated; atomic and single use |
| `POST` | `users/auth/password-reset/request/` | Public + CSRF; generic account-enumeration-safe response |
| `POST` | `users/auth/password-reset/confirm/` | Public + CSRF; hashed, expiring, single-use token |

Registration, login, refresh, password-sensitive routes, and invitation acceptance have dedicated
DRF throttle scopes. Auth error messages do not reveal whether an arbitrary email exists. In debug
only, invitation/reset URLs can be returned to an authorized workflow; production delivery is not
claimed until a mail provider is configured.

## Organizations and onboarding

| Method | Route | Permission |
| --- | --- | --- |
| `GET,POST` | `organizations/` | Authenticated; POST creates an owned tenant |
| `GET,PATCH` | `organizations/{org}/` | Active member / `manage_settings` |
| `GET,PATCH` | `organizations/{org}/profile/` | Active member / `manage_settings` |
| `GET,PATCH` | `organizations/{org}/onboarding/` | Active member / `manage_settings` |
| `GET` | `organizations/{org}/overview/` | Active member |
| `GET,POST` | `organizations/{org}/branches/` | Active member / `manage_settings` |
| `GET,PATCH,DELETE` | `organizations/{org}/branches/{branch}/` | Active member / `manage_settings`; DELETE archives |
| `GET` | `organizations/{org}/memberships/` | Active member |
| `PATCH` | `organizations/{org}/memberships/{membership}/` | `manage_team`; last-owner and role-elevation checks |
| `GET,POST` | `organizations/{org}/invitations/` | Active member / `manage_team` |
| `PATCH` | `organizations/{org}/invitations/{invitation}/` | `manage_team`; revoke pending only |

The onboarding PATCH accepts partial `organization`, `profile`, `assistant_context`, optional
`branch`, `step` (1–6), and `complete`. Each valid step is persisted and resumable. Completion is
atomic and fails with `onboarding_incomplete` plus missing field names until required company, first
branch, and AI Context values exist.

Branch `working_hours` is a weekday-keyed JSON object (`mon`…`sun`) containing ordered
`{"open":"09:00","close":"18:00"}` periods. Invalid days, times, shapes, and inverted periods are
rejected server-side.

## AI Context

| Method | Route | Permission |
| --- | --- | --- |
| `GET,PATCH` | `assistant-context/` | Active member / centralized `manage_settings` |
| `POST` | `assistant-context/publish/` | Centralized `manage_settings` |
| `GET` | `assistant-context/revisions/` | Active member |

PATCH always returns the working document to `draft`. Publish validates required fields, takes a
transactional snapshot, increments the tenant-local version, and records actor/time. Later draft
edits do not modify the previously published snapshot. Inputs are plain text; HTML is rejected.
No OpenAI request or system-prompt generation occurs.

## Channels and legacy routes

- `GET,POST channel-connections/` and detail routes show tenant-owned status and safe metadata.
- Credentials, encrypted values, and webhook secret hashes are write-only and never serialized.
- Tenant-scoped legacy jobs, knowledge-base, intake, and escalation routes remain preserved.
- `POST public/early-access/` is the server-to-server Landing lead route.
- `GET /health/live` and `GET /health/ready` are outside the versioned prefix.

## Errors and isolation

Missing/invalid tenant headers return stable `400` codes. Inactive or absent membership returns
`403`; a path/header mismatch is rejected; tenant-owned object IDs from another valid selected
tenant return `404`. Suspended/archived organizations permit safe reads only. Customer APIs do not
give Django superusers an implicit tenant bypass. Error responses carry the request ID when
available; logs contain request metadata rather than cookies, tokens, credentials, or form bodies.
