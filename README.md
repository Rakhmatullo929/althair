# AI Front Office workspace

This repository contains the existing Next.js landing workspace and a tenant-safe Django modular
monolith.

## Backend services

```bash
cp backend/.env.example .env
# Fill local placeholders; do not commit .env.
docker compose up --build
docker compose exec api python manage.py createsuperuser
docker compose exec api python manage.py seed_dev_workspace
```

The API is available at `http://localhost:8000`; health routes are `/health/live` and
`/health/ready`.

## Landing

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Configure its server-side lead delivery with:

```dotenv
LEAD_WEBHOOK_URL=http://localhost:8000/api/v1/public/early-access/
LEAD_WEBHOOK_SECRET=replace-with-the-same-random-value
```

The secret is read only by the Next.js server route and must never use a `NEXT_PUBLIC_` name.
