# AI Front Office backend

Django 5.2 modular monolith for secure client authentication, organization onboarding, versioned AI
Context, channel configuration status, and the preserved tenant-safe legacy intake/job workflows.

## Local Python setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Replace placeholders; generate FIELD_ENCRYPTION_KEY exactly as the example describes.
python manage.py migrate --noinput
CLIENT_PORTAL_SEED_PASSWORD='development-only-change-me' python manage.py seed_client_portal
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
| `EARLY_ACCESS_WEBHOOK_SECRET` | Landing server-to-server lead authentication |
| `OPENAI_API_KEY`, provider credentials/secrets | Reserved legacy/later integrations; never browser-exposed |
| `DEV_*_DESTINATION_*` | Non-secret legacy development destination routing |

Every variable is represented by an empty or fake placeholder in `.env.example`. Never commit a
populated `.env`.

## Checks

```bash
python manage.py makemigrations --check
python manage.py migrate --noinput
python manage.py check
python manage.py test
python -m compileall -q .
```

Architecture and contracts:

- `docs/security/client-authentication.md`
- `docs/architecture/multitenancy.md`
- `docs/architecture/client-onboarding.md`
- `docs/api/multitenant-api.md`
- `docs/security/secret-rotation-required.md`

## Known limitations

Production email delivery, provider activation/OAuth, OpenAI execution, generated prompts, CRM,
booking, billing, Super Admin, RLS, and production deployment are outside this stage. The legacy MMC
vertical is intentionally retained and independently regression-tested.
