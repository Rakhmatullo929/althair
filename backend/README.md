# AI Front Office backend

Django 5.2 modular monolith for secure client authentication, onboarding, versioned AI Context,
channel configuration status, a tenant-safe CRM, an approval-controlled AI conversation runtime,
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
| `META_APP_ID`, `META_APP_SECRET`, `META_INSTAGRAM_VERIFY_TOKEN` | Server-only Instagram Business Login and signed webhook configuration |
| `META_INSTAGRAM_GRAPH_API_VERSION`, `META_INSTAGRAM_REDIRECT_URI` | Explicit current Graph version and exact OAuth callback |
| `META_INSTAGRAM_ENABLE_LIVE`, `META_INSTAGRAM_ENABLE_HUMAN_AGENT` | Fail-closed live-provider and separately approved human-only extension gates |
| `META_INSTAGRAM_FAKE_PROVIDER` | Deterministic debug/test/E2E adapter; forced off in production compose |
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
python -m compileall -q .
python evals/ai_runtime/run_evals.py
```

Architecture and contracts:

- `docs/security/client-authentication.md`
- `docs/architecture/multitenancy.md`
- `docs/architecture/client-onboarding.md`
- `docs/architecture/crm-core.md`
- `docs/architecture/ai-conversation-runtime.md`
- `docs/architecture/instagram-messaging.md`
- `docs/api/multitenant-api.md`
- `docs/api/crm-api.md`
- `docs/api/ai-runtime-api.md`
- `docs/api/instagram-messaging-api.md`
- `docs/integrations/instagram-app-review.md`
- `docs/security/secret-rotation-required.md`

## Known limitations

Production email delivery, Telegram and other external providers, booking, billing, Super Admin,
RLS, and production deployment are outside this stage. Instagram live access remains gated by real
Meta configuration and App Review; local/CI coverage uses the deterministic fake adapter. The live
Responses API remains opt-in and is never required for tests. The legacy MMC vertical is retained
and independently regression-tested.
