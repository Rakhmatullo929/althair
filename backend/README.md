# AI Front Office backend

Django 5.2 modular monolith for secure client authentication, onboarding, versioned AI Context,
channel configuration status, a tenant-safe CRM, an approval-controlled AI conversation runtime,
inbound Voice AI telephony, separate Internal Super Admin operations, provider-independent Billing,
and the preserved legacy intake/job workflows.

## Local Python setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Replace placeholders; generate FIELD_ENCRYPTION_KEY exactly as the example describes.
python manage.py migrate --noinput
CLIENT_PORTAL_SEED_PASSWORD='development-only-change-me' python manage.py seed_client_portal
ENABLE_CRM_TEST_CHANNEL=true python manage.py seed_crm
python manage.py runserver 0.0.0.0:8000
```

PostgreSQL and Redis are the supported runtime services. SQLite via `USE_SQLITE=1` and an optional
`SQLITE_PATH` is for isolated local/test verification only. `seed_client_portal` refuses to run
unless `DEBUG=true` and `CLIENT_PORTAL_SEED_PASSWORD` is set.

## Environment

| Variable | Purpose |
| --- | --- |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `ADMIN_URL` | Django runtime security |
| `CORS_ALLOWED_ORIGINS`, `CSRF_TRUSTED_ORIGINS` | Exact Landing/client browser origins |
| `CLIENT_APP_URL` | Debug invitation/reset link origin |
| `EMAIL_BACKEND`, `DEFAULT_FROM_EMAIL` | Development console lifecycle; production provider pending |
| `POSTGRES_*`, `REDIS_URL` | Database, shared cache, throttle, and queue services |
| `FIELD_ENCRYPTION_KEY` | Fernet key for write-only provider credential storage |
| `CLIENT_PORTAL_SEED_PASSWORD` | Required local deterministic user password; never printed |
| `ENABLE_CRM_TEST_CHANNEL` | Enables the internal development-only CRM channel; default false |
| `AI_RUNTIME_PROVIDER` | `fake` by default; set `openai` only for an explicit live workflow |
| `AI_RUNTIME_ENABLE_REAL_OPENAI` | Additional server-side gate for all real Responses API calls |
| `AI_INTERNAL_TEST_AUTOPILOT` | Allows auto-send only on the internal channel in dev/test |
| `OPENAI_MODEL` | Explicit Responses API model alias; no model is selected in the browser |
| `OPENAI_REQUEST_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES` | Bounded live-provider request policy |
| `AI_MANUAL_GENERATION_PER_MINUTE`, `AI_MAX_TOOL_CALLS_PER_RUN` | Abuse and action-loop caps |
| `VOICE_ENABLE_LIVE`, `VOICE_*_PROVIDER`, `VOICE_GLOBAL_KILL_SWITCH` | Fail-closed live/fake Voice selection and emergency gate |
| `TWILIO_VOICE_*` | Server-only Voice account, API/auth, number/trunk, and public callback configuration |
| `OPENAI_WEBHOOK_SECRET`, `OPENAI_PROJECT_ID`, `OPENAI_REALTIME_*` | Signed Realtime SIP call and explicit session configuration |
| `VOICE_MAX_*`, `VOICE_*_MINUTE_LIMIT`, `VOICE_TRANSCRIPT_RETENTION_DAYS` | Duration, concurrency, cost, and privacy defaults |
| `BILLING_ENABLE`, `BILLING_PROVIDER`, `BILLING_FAKE_PROVIDER` | Explicit Billing gate and deterministic fake/manual provider selection |
| `BILLING_DEFAULT_PLAN_KEY`, `BILLING_DEFAULT_CURRENCY` | Versioned default plan and explicit ISO currency |
| `BILLING_TRIAL_DAYS`, `BILLING_GRACE_DAYS`, `BILLING_INVOICE_PREFIX` | Trial, dunning, and invoice-number policy |
| `BILLING_MANUAL_PROVIDER_ENABLE` | Enables reviewed manual pilot operations; never online checkout |
| `META_APP_ID`, `META_APP_SECRET`, `META_INSTAGRAM_VERIFY_TOKEN` | Server-only Instagram Business Login and signed webhook configuration |
| `META_INSTAGRAM_GRAPH_API_VERSION`, `META_INSTAGRAM_REDIRECT_URI` | Explicit current Graph version and exact OAuth callback |
| `META_INSTAGRAM_ENABLE_LIVE`, `META_INSTAGRAM_ENABLE_HUMAN_AGENT` | Fail-closed live-provider and separately approved human-only extension gates |
| `META_INSTAGRAM_FAKE_PROVIDER` | Deterministic debug/test/E2E adapter; forced off in production compose |
| `TELEGRAM_ENABLE_LIVE`, `TELEGRAM_FAKE_PROVIDER` | Explicit live gate and debug/test-only deterministic adapter |
| `TELEGRAM_MANAGER_BOT_TOKEN`, `TELEGRAM_MANAGER_BOT_USERNAME` | Server-only manager bot used for official managed-bot APIs |
| `TELEGRAM_MANAGER_WEBHOOK_URL`, `TELEGRAM_MANAGER_WEBHOOK_SECRET` | Signed manager updates; HTTPS is required live |
| `TELEGRAM_BOT_WEBHOOK_BASE_URL` | HTTPS base for opaque per-bot webhook paths |
| `GOOGLE_GMAIL_ENABLE_LIVE`, `GOOGLE_GMAIL_FAKE_PROVIDER` | Fail-closed live Gmail gate and deterministic debug/test provider |
| `GOOGLE_GMAIL_CLIENT_ID`, `GOOGLE_GMAIL_CLIENT_SECRET`, `GOOGLE_GMAIL_REDIRECT_URI` | Server-only Google OAuth web application values |
| `GOOGLE_GMAIL_PUBSUB_TOPIC`, `GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION` | Exact Gmail watch topic and push subscription resources |
| `GOOGLE_GMAIL_PUBSUB_AUDIENCE`, `GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT` | Authenticated Pub/Sub OIDC identity constraints |
| `EARLY_ACCESS_WEBHOOK_SECRET` | Landing server-to-server lead authentication |
| `OPENAI_API_KEY`, provider credentials/secrets | Server-only; empty for fake-provider/CI workflows |
| `DEV_*_DESTINATION_*` | Non-secret legacy development destination routing |

Every variable is represented by an empty or fake placeholder in `.env.example`. Never commit a
populated `.env`.

## Checks

```bash
python manage.py makemigrations --check
python manage.py migrate --noinput
python manage.py check
python manage.py test
coverage run --source=ai_runtime manage.py test ai_runtime
coverage report --fail-under=85
coverage run --source=billing manage.py test billing
coverage report --fail-under=85
python -m compileall -q .
python evals/ai_runtime/run_evals.py
python evals/voice/run_evals.py
```

Architecture and contracts:

- `docs/security/client-authentication.md`
- `docs/architecture/multitenancy.md`
- `docs/architecture/client-onboarding.md`
- `docs/architecture/crm-core.md`
- `docs/architecture/ai-conversation-runtime.md`
- `docs/architecture/instagram-messaging.md`
- `docs/architecture/telegram-managed-bots.md`
- `docs/architecture/gmail-email-integration.md`
- `docs/architecture/voice-ai-telephony.md`
- `docs/architecture/internal-control-plane.md`
- `docs/architecture/billing-subscriptions.md`
- `docs/api/multitenant-api.md`
- `docs/api/crm-api.md`
- `docs/api/ai-runtime-api.md`
- `docs/api/instagram-messaging-api.md`
- `docs/api/telegram-managed-bots-api.md`
- `docs/api/gmail-email-api.md`
- `docs/api/voice-ai-api.md`
- `docs/api/internal-control-plane-api.md`
- `docs/api/billing-api.md`
- `docs/operations/billing-runbook.md`
- `docs/integrations/instagram-app-review.md`
- `docs/integrations/telegram-managed-bots.md`
- `docs/integrations/google-gmail-setup.md`
- `docs/integrations/twilio-openai-voice-setup.md`
- `docs/security/secret-rotation-required.md`

## Known limitations

Generic email/IMAP, Outlook, WhatsApp, outbound Voice, recording, booking, live payment providers,
tax/fiscalization, RLS, and production deployment are outside this stage. Instagram live access remains gated by real
Meta configuration and App Review; local/CI coverage uses the deterministic fake adapter. The live
Responses API remains opt-in and is never required for tests. The legacy MMC vertical is retained
and independently regression-tested.
