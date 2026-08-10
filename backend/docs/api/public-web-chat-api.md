# Public Web Chat API and operations

## Installation lifecycle

Owner/admin endpoints under `/api/v1/web-chat/installations/` require cookie authentication and `X-Organization-ID`. Create a draft, configure exact HTTPS origins (HTTP is accepted only for local development), optionally enable published-context AI, then activate. Pause stops new traffic; revoke also blocks active sessions. `rotate-key/` changes the public identifier and records a hash of the previous key. Signing and session secrets are never returned.

Embed version 1:

```html
<script
  src="https://chat.example.com/widget.js?v=1"
  data-installation-key="wc_live_..."
  data-app-url="https://chat.example.com"
  data-api-url="https://api.example.com/api/v1"
  data-locale="en"
  async
></script>
```

Allow `script-src https://chat.example.com`, `frame-src https://chat.example.com`, and `connect-src https://api.example.com` in the host CSP. Do not add an API secret. Origins are scheme/host/port exact; wildcards and paths are rejected.

## Visitor API

- `GET /api/v1/public/web-chat/installations/{key}/config/`
- `POST /api/v1/public/web-chat/installations/{key}/sessions/`
- `GET|POST /api/v1/public/web-chat/sessions/{session}/messages/`
- `GET /api/v1/public/web-chat/sessions/{session}/events/`
- `PATCH .../identity/`; `POST .../handoff/`, `.../read/`, `.../close/`, `.../resume/`

Session creation sends the config's short-lived `origin_proof`, selected language, and consent flag. Subsequent requests use `Authorization: Bearer <opaque-session-token>` and never `X-Organization-ID`. Message POST requires a unique `Idempotency-Key`; retries return the original CRM message. Messages are plain text and limited to 4,000 characters. SSE reconnect sends `Last-Event-ID`; polling sends `?after=<cursor>`. Safe error codes accompany 401/403, 409, and 429 without tenant IDs.

## Local demo

Enable only synthetic local data:

```bash
WEB_CHAT_ENABLE_PUBLIC=true WEB_CHAT_ALLOW_FAKE_AUTOPILOT=true \
WEB_CHAT_WIDGET_ORIGINS=http://localhost:3001 \
python manage.py seed_web_chat_demo
```

Set `NEXT_PUBLIC_WEB_CHAT_DEMO_KEY` in the Client app and open `/{locale}/demo`. The seed is idempotent and does not print credentials. CI never needs OpenAI. A bounded live smoke is separately opt-in: set the real-provider gates and run `python manage.py smoke_openai_responses --confirm-live`; it uses synthetic text only.

## Production and troubleshooting

Keep `WEB_CHAT_ENABLE_PUBLIC=false` until the widget origin, HTTPS/CSP, signing key, rate limits, retention policy, and organization legal copy are reviewed. Configure `WEB_CHAT_SESSION_SIGNING_KEY`, `WEB_CHAT_WIDGET_BASE_URL`, `WEB_CHAT_WIDGET_ORIGINS`, session/rate limits, and both global kill switches. Keep `WEB_CHAT_ALLOW_FAKE_AUTOPILOT=false` in production.

An unavailable config usually means disabled public access, inactive/revoked installation, suspended organization, or origin mismatch. A 401 means missing/expired/rotated credential or origin mismatch. A 429 means deterministic abuse protection. Provider outages create a CRM handoff; inspect safe run/error categories and installation metrics, never visitor text or credentials in logs.
