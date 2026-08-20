# Local development setup

This guide preserves the detailed setup and verification commands that previously lived in the root README. The concise [project overview](../../README.md) remains the canonical product introduction.

## Prerequisites

- Docker with Compose v2
- Python 3.12 for optional native backend development
- Node.js 24 or newer (`frontend/.nvmrc`)
- Corepack and pnpm 11.21.0 (`frontend/package.json`)

PostgreSQL 16 and Redis 7 are the supported services. SQLite is limited to isolated local/test checks and is not the normal runtime path.

## Environment files

From the repository root:

```bash
cp backend/.env.example .env
cp frontend/.env.example frontend/.env.local
```

Review every local placeholder. Keep both files untracked. Never place production credentials in a local README command, shell history, URL, screenshot, or Git commit. The deterministic fake provider path does not require real OpenAI, Meta, Google, Telegram, or Twilio credentials.

## Backend with Docker

```bash
docker compose up -d --build
docker compose exec api python manage.py migrate --noinput
docker compose exec api python manage.py check
```

Services:

- API: `http://localhost:8000`
- liveness: `http://localhost:8000/health/live`
- readiness: `http://localhost:8000/health/ready`
- development Swagger: `http://localhost:8000/swagger/`
- workers: Celery and the Voice gateway worker

The standard compose stack uses PostgreSQL and Redis volumes and development-only defaults. It is not a production secret configuration.

## Frontend workspace

```bash
cd frontend
corepack enable
pnpm install --frozen-lockfile
```

Run each application in its own terminal:

```bash
pnpm dev          # Landing: http://localhost:3000
pnpm dev:client   # Client Portal: http://localhost:3001
pnpm dev:admin    # Internal Admin: http://localhost:3002
```

The Landing uses `NEXT_PUBLIC_CLIENT_APP_URL`. Client and Admin use their API variables and same-origin rewrites when `BACKEND_API_ORIGIN` is configured.

## Safe platform bootstrap

The production-safe command creates the first platform owner as a separate `PlatformStaffAccess`, not as a customer membership or superuser bypass. Supply the password through stdin or a restricted file; the command never prints it or an MFA secret.

```bash
read -s ALTAIR_BOOTSTRAP_PASSWORD
printf '%s\n' "$ALTAIR_BOOTSTRAP_PASSWORD" | docker compose exec -T api \
  python manage.py bootstrap_platform --non-interactive \
  --owner-email owner@example.test --password-stdin --create-wallets --safe-json-report
unset ALTAIR_BOOTSTRAP_PASSWORD

docker compose exec api python manage.py bootstrap_platform \
  --check --owner-email owner@example.test
```

Replace the synthetic email only in your private environment configuration. For release automation, prefer `--password-file /run/secrets/platform-owner-password` with a deployment-managed secret mount.

## Deterministic full demo

`seed_full_demo` creates only synthetic Client Portal, control-plane, CRM, Web Chat, channel, Billing, Wallet, and Booking records. It is restricted to development, staging, and tests; production rejects both seed and reset before changing data.

```bash
read -s FULL_DEMO_SEED_PASSWORD
export FULL_DEMO_SEED_PASSWORD
docker compose exec -e DEPLOYMENT_ENVIRONMENT=development \
  -e FULL_DEMO_SEED_PASSWORD api python manage.py seed_full_demo \
  --organization-slug mehr-clinic --with-admin --with-wallet \
  --non-interactive --safe-json-report
unset FULL_DEMO_SEED_PASSWORD
```

Use a unique local value of at least 12 characters. Do not reuse a real account password. The command never includes the password in its report.

Individual deterministic fixtures remain available when a full demo is unnecessary:

```bash
read -s CLIENT_PORTAL_SEED_PASSWORD
export CLIENT_PORTAL_SEED_PASSWORD
docker compose exec -e DEBUG=true -e CLIENT_PORTAL_SEED_PASSWORD api \
  python manage.py seed_client_portal
unset CLIENT_PORTAL_SEED_PASSWORD

docker compose exec -e DEBUG=true -e ENABLE_CRM_TEST_CHANNEL=true api python manage.py seed_crm
docker compose exec -e DEBUG=true -e WEB_CHAT_ENABLE_PUBLIC=true \
  -e WEB_CHAT_ALLOW_FAKE_AUTOPILOT=true api python manage.py seed_web_chat_demo
docker compose exec -e DEBUG=true -e E2E_TESTING=true -e BILLING_ENABLE=true \
  -e BILLING_PROVIDER=fake api python manage.py seed_billing_demo
docker compose exec -e DEBUG=true -e BOOKING_ENABLE=true api python manage.py seed_booking_demo
```

Commands that create login users require their documented password environment variable. Do not substitute a weak value in shared documentation.

## Provider modes

- AI Runtime: deterministic fake by default; real OpenAI requires the explicit provider, enable gate, model and server key.
- Instagram, Telegram, Gmail, SMS and Voice: fake/no-network paths are available for development and CI; live mode is fail-closed until its setup guide is satisfied.
- Booking reminders: deterministic fake or an existing consent-aware channel path.
- Billing: deterministic fake and reviewed manual only; no live payment provider or card collection.
- Internal MFA: deterministic fake code is accepted only when fake MFA is enabled in debug/test; production startup rejects it.

## Native backend option

```bash
cd backend
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python manage.py migrate --noinput
python manage.py runserver 0.0.0.0:8000
```

Keep `.venv/`, databases, caches and populated `.env` files untracked.

## Verification

Backend:

```bash
cd backend
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
python evals/booking/run_evals.py
```

Frontend and repository:

```bash
cd frontend
pnpm format:check
pnpm lint
pnpm typecheck
pnpm build
pnpm test
pnpm test:e2e

cd ..
./scripts/check-secrets.sh
git diff --check
```

Screenshot suites generate local test artifacts; do not commit their output wholesale.

## Production-shaped backend check

`docker-compose.prod.yml` keeps PostgreSQL and Redis on an internal network, runs the API, Celery and Voice worker with restart/logging policies, and expects an existing reverse-proxy network. It is deployment scaffolding, not proof of a production backend deployment.

```bash
cp backend/.env.production.example .env.production
# Replace every placeholder using the deployment secret manager.
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T api \
  python manage.py check --deploy
```

Review the Nginx examples in `deploy/nginx/`, rotate all provider credentials, validate backups and monitoring, and complete provider-specific sandbox checks before any live activation.
