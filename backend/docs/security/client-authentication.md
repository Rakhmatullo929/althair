# Client authentication architecture

The portal uses the existing Django user model, DRF SimpleJWT, and a single cookie transport. It
does not maintain a competing browser bearer-token system.

## Browser flow

1. The client fetches `GET /api/v1/users/auth/csrf/` with credentials included.
2. Unsafe calls send the readable `csrftoken` cookie as `X-CSRFToken`.
3. Login or registration sets short-lived access and longer-lived refresh JWTs in HttpOnly cookies.
4. The API client retries one `401` after a CSRF-protected refresh. It never persists bearer tokens
   in `localStorage` or `sessionStorage`.
5. Logout blacklists the refresh token and clears both cookies.

Production cookie security is controlled by `DEBUG`: cookies are `Secure` outside debug and use an
appropriate SameSite policy. CORS allows credentials only for explicit configured origins;
`CSRF_TRUSTED_ORIGINS` must match deployed browser origins exactly.

## Registration and recovery

Registration runs in one database transaction: normalized unique email user, organization,
organization profile, AI Context root, and active owner membership. Django password validators run
before persistence. Conflicts use a generic response.

Invitation and reset secrets are random opaque values; only SHA-256 hashes are stored. Tokens
expire, are single-use, and are submitted to the backend in a JSON body. Invitation acceptance
locks the invitation row and atomically activates the correct-email membership. Reset use
invalidates other outstanding reset tokens for that user.

The console mail/debug URL behavior is development-only. Production email delivery remains pending
and API responses do not claim a message was sent by a provider that is not configured.

## Abuse and telemetry controls

Login, registration, refresh, invitation acceptance, password reset, and password change use
separate DRF throttle scopes. Production uses the configured shared cache. Security logs reference
internal user/organization/request IDs and event types; they do not include passwords, cookies, raw
tokens, credential fields, or sensitive bodies. Failure wording avoids email-account enumeration.
