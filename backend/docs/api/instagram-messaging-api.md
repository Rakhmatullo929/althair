# Instagram Messaging API

Tenant routes require an authenticated session, `X-Organization-ID`, and the existing role matrix. Owner/admin mutate connections; health is always safe and token-free.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/integrations/instagram/` | List tenant connections |
| `GET` | `/api/v1/integrations/instagram/oauth/start/?redirect=...` | Create bound OAuth state |
| `GET` | `/api/v1/integrations/instagram/oauth/callback/?state=...&code=...` | Complete server-side exchange; organization comes from state |
| `GET/PATCH` | `/api/v1/integrations/instagram/{id}/` | Read safe health or change `automation_mode` |
| `POST` | `/api/v1/integrations/instagram/{id}/disconnect/` | Clear encrypted credentials and audit disconnect |
| `POST` | `/api/v1/integrations/instagram/{id}/reconnect/` | Development fake reconnect; live mode requires OAuth |
| `GET/POST` | `/api/v1/integrations/instagram/{id}/health/` | Read or refresh safe health |
| `POST` | `/api/v1/integrations/instagram/{id}/backfill/` | Queue bounded recent reconciliation |
| `GET` | `/api/v1/integrations/instagram/{id}/operations/` | Queue/event/dead-letter summary |
| `GET/POST` | `/api/v1/webhooks/instagram/` | Meta verification and signed receipt |

Development-only `test-event` and `test-control` routes work only with the explicit fake provider in DEBUG/test/E2E and are unavailable in live mode.

Conversation responses expose a safe `provider_context`. The existing message route accepts `human_agent: true` only for an eligible logged-in employee; the backend independently rechecks all policy. Safe machine codes include `window_expired`, `connection_expired`, `permission_missing`, and `provider_policy_rejected`.

No response includes an access token, Meta app secret, webhook verify token, raw provider event, or hidden AI reasoning.
