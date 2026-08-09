# AI Front Office workspace

This repository contains the preserved public Landing, the localized customer portal with a real
CRM workflow, and a tenant-safe Django modular monolith. The legacy MMC vertical is still isolated
in the backend and remains covered by its regression tests.

## Local stack

```bash
cp backend/.env.example .env
# Replace local placeholders. Never commit .env.
docker compose up -d --build
docker compose exec api python manage.py migrate --noinput

cd frontend
corepack enable
pnpm install --frozen-lockfile
pnpm dev                 # Landing at http://localhost:3000
pnpm dev:client          # Client portal at http://localhost:3001
```

The API is at `http://localhost:8000`; health routes are `/health/live` and `/health/ready`.
The Landing uses `NEXT_PUBLIC_CLIENT_APP_URL` for its login entry point. The portal uses
`NEXT_PUBLIC_API_URL` and sends credentialed cookie requests to the API.

## Deterministic portal data

The seed command is deliberately development-only and requires an explicit password:

```bash
cd backend
DEBUG=true CLIENT_PORTAL_SEED_PASSWORD='development-only-change-me' \
  python manage.py seed_client_portal
```

It creates `owner@portal.test`, a lower-role member, active multi-organization data, a suspended
organization, branches, channel status records, and versioned AI Context. The password is never
printed. `seed_dev_workspace` remains available for the preserved legacy development fixtures.

CRM fixtures are separate and deterministic:

```bash
DEBUG=true ENABLE_CRM_TEST_CHANNEL=true python manage.py seed_crm
```

The internal development channel is disabled by default and can only be enabled server-side with
`ENABLE_CRM_TEST_CHANNEL=true`. It never impersonates a real provider connection.

## Verification

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python manage.py makemigrations --check
python manage.py migrate --noinput
python manage.py check
python manage.py test
coverage run --source=crm manage.py test crm
coverage report
python -m compileall -q .

cd ../frontend
pnpm install --frozen-lockfile
pnpm format:check
pnpm lint
pnpm typecheck
pnpm build
pnpm test
pnpm test:e2e
pnpm screenshots

cd ..
./scripts/check-secrets.sh
```

See [backend/docs/api/multitenant-api.md](backend/docs/api/multitenant-api.md),
[backend/docs/api/crm-api.md](backend/docs/api/crm-api.md),
[backend/docs/security/client-authentication.md](backend/docs/security/client-authentication.md),
and [backend/docs/architecture/crm-core.md](backend/docs/architecture/crm-core.md).

## Current boundary

This stage intentionally does not activate Instagram, Telegram, Gmail, WhatsApp, SMS, or Voice;
run OpenAI; add booking, billing, or Super Admin; or deploy production infrastructure. CRM content
is real organization-owned database data, not simulated AI, revenue, sales, or provider state.
Password-reset and invitation email delivery use the development console lifecycle only until a
reliable production mail provider is configured.
