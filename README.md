# AI Front Office workspace

This repository contains the preserved public Landing, the localized customer portal with a real
CRM workflow, tenant-owned Web Chat, Instagram and Telegram messaging, and a tenant-safe,
approval-controlled AI conversation runtime. The legacy MMC
vertical is still isolated in the backend and remains covered by its regression tests.

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

The opt-in public Web Chat demo is also separate and uses synthetic organization data:

```bash
DEBUG=true WEB_CHAT_ENABLE_PUBLIC=true WEB_CHAT_ALLOW_FAKE_AUTOPILOT=true \
  python manage.py seed_web_chat_demo
```

The AI runtime uses the deterministic fake provider by default. Publish AI Context, enable the
runtime in **Settings → AI Automation**, and allow the internal test channel. Optional internal
autopilot also requires the server-side `AI_INTERNAL_TEST_AUTOPILOT=true` flag. Real OpenAI
Responses API calls are opt-in and never required by CI; see the architecture document below.

## Verification

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python manage.py makemigrations --check
python manage.py migrate --noinput
python manage.py check
python manage.py test
coverage run --source=ai_runtime manage.py test ai_runtime
coverage report --fail-under=85
python -m compileall -q .
python evals/ai_runtime/run_evals.py

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

## Production backend

Production uses `docker-compose.prod.yml`: PostgreSQL and Redis stay on an internal Docker network,
only the API joins the existing reverse-proxy network, containers restart automatically, and Docker
logs are rotated. Copy `backend/.env.production.example` to `.env.production`, replace every
placeholder, and run:

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
docker compose --env-file .env.production -f docker-compose.prod.yml exec -T api \
  python manage.py check --deploy
```

The portal should use `NEXT_PUBLIC_API_URL=/api/v1` and `BACKEND_API_ORIGIN` as the HTTPS API
origin. Its Next.js rewrite keeps browser authentication same-origin while the reverse proxy
terminates TLS in front of Django. Nginx bootstrap and TLS configs live in `deploy/nginx/`.

See [backend/docs/api/multitenant-api.md](backend/docs/api/multitenant-api.md),
[backend/docs/api/crm-api.md](backend/docs/api/crm-api.md),
[backend/docs/security/client-authentication.md](backend/docs/security/client-authentication.md),
[backend/docs/architecture/crm-core.md](backend/docs/architecture/crm-core.md),
[backend/docs/architecture/ai-conversation-runtime.md](backend/docs/architecture/ai-conversation-runtime.md),
[backend/docs/api/ai-runtime-api.md](backend/docs/api/ai-runtime-api.md),
[backend/docs/architecture/public-web-chat.md](backend/docs/architecture/public-web-chat.md), and
[backend/docs/api/public-web-chat-api.md](backend/docs/api/public-web-chat-api.md),
[backend/docs/architecture/telegram-managed-bots.md](backend/docs/architecture/telegram-managed-bots.md), and
[backend/docs/api/telegram-managed-bots-api.md](backend/docs/api/telegram-managed-bots-api.md).

## Current boundary

This stage intentionally does not activate Gmail, WhatsApp, SMS, or Voice; add booking, billing,
or Super Admin; or deploy production infrastructure. Public Web Chat, Instagram Messaging, and
Telegram Managed Bots remain server-side opt-in. Real OpenAI calls stay
disabled unless explicitly enabled server-side. CRM content is real organization-owned database
data, not simulated revenue, sales, or provider state.
Password-reset and invitation email delivery use the development console lifecycle only until a
reliable production mail provider is configured.
