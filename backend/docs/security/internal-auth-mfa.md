# Internal authentication and MFA

## Session separation

Internal authentication uses only a dedicated opaque cookie scoped to
`/api/v1/internal/`. The random token is shown only to the browser; only its SHA-256
hash is stored. It has a short absolute lifetime, an inactivity timeout, `HttpOnly`,
`SameSite=Strict`, and `Secure` outside debug mode. Unsafe requests require CSRF.
Login and MFA endpoints have independent rate limits, failures are generic, and
session revocation is immediate.

Customer sessions, JWTs, membership roles, and Django superuser flags are ignored by
the internal authenticator. Suspended/revoked staff and inactive users fail closed.
Production can additionally restrict source IPs with `CONTROL_PLANE_ALLOWED_IPS`.

## MFA

TOTP follows RFC 6238-compatible 30-second SHA-1 time steps with a one-step clock
window. The shared secret uses the repository encrypted field. Replayed time steps are
rejected. Recovery codes are one-time values stored only as salted application hashes;
setup material is returned once. MFA and recovery values must never be logged.

The deterministic `000000` path is accepted only when the fake-MFA setting is enabled
and the process is in debug or test mode. Startup rejects fake MFA in production.
Privileged internal writes require a recent MFA timestamp.

## Provisioning and break glass

Production staff provisioning is owner-only. Start with at least two active owners,
verify MFA before granting other roles, and revoke abandoned sessions after every
role/status change. The demo seed command creates synthetic addresses only and
requires its password through an environment variable.

There is no enabled break-glass endpoint. If access recovery is required, use a
documented, two-person operational process at the database/identity layer, rotate the
affected credentials, review audit records, and remove the temporary access. Never
disable tenant isolation or make customer APIs accept an internal role.

## Production hardening checklist

- Keep `CONTROL_PLANE_ENABLE=false` until the separate admin origin and routing are
  explicitly deployed.
- Set a unique internal cookie name, a strong Django secret, encrypted-field key, TLS,
  trusted CSRF/CORS origins, and a narrow IP allow-list where practical.
- Require MFA for every active staff identity; keep fake MFA disabled.
- Use independent staff identities, least-privilege roles, two active owners, and
  regular session/access review.
- Send audit records to immutable retention and alert on login failures, owner changes,
  emergency controls, exports, and destructive approvals.
- Use encrypted temporary object storage with short-lived signed links before enabling
  real exports.
- Exercise control activation/restore, session revocation, and owner recovery in a
  non-production environment.
