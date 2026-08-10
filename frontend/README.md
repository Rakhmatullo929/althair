# AI Front Office frontend

Next.js 16/React 19 monorepo for the public Landing and authenticated customer portal. Both apps
share the brand and UI packages while keeping customer product routes out of the Landing.

## Structure

```text
frontend/
├── apps/
│   ├── landing/              Public RU/UZ/EN marketing site on :3000
│   └── client/               Auth, onboarding, settings, and CRM on :3001
├── packages/
│   ├── api-client/           Typed cookie/CSRF and tenant-aware API client
│   ├── brand/                Central product name, links, and logo
│   ├── ui/                   Shared accessible primitives
│   ├── eslint-config/
│   └── typescript-config/
└── artifacts/screenshots/    Ignored, generated browser evidence
```

The client includes login/registration/invitation/reset states; a responsive application shell;
validated organization switching; resumable onboarding; company, branch, team, channel-status,
versioned AI Context, Unified Inbox, contacts, leads, and follow-up tasks. RU, UZ, and EN
dictionaries are complete. The CRM uses only organization-scoped API data. Real provider
activation, OpenAI execution, booking, billing, and Super Admin are intentionally absent.

## Commands

Node 24 and the pinned pnpm version are required.

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm dev
pnpm dev:client
pnpm format:check
pnpm lint
pnpm typecheck
pnpm build
pnpm test
pnpm --filter @workspace/client exec playwright install chromium
pnpm test:e2e
pnpm screenshots
```

Client E2E creates an isolated SQLite database in `/tmp`, enables the server-only internal test
channel, seeds deterministic portal and CRM data, and runs without any provider or production email
dependency. Screenshots are written below `artifacts/screenshots/client/` and remain ignored by Git.

## Environment

Copy `.env.example` to an ignored local env file.

| Variable                     | Exposure    | Purpose                                                  |
| ---------------------------- | ----------- | -------------------------------------------------------- |
| `NEXT_PUBLIC_APP_URL`        | Browser     | Canonical Landing origin                                 |
| `NEXT_PUBLIC_CLIENT_APP_URL` | Browser     | Portal origin used by Landing login/start links          |
| `NEXT_PUBLIC_API_URL`        | Browser     | Portal API base, normally `http://localhost:8000/api/v1` |
| `NEXT_PUBLIC_BRAND_*`        | Browser     | Temporary centralized product identity                   |
| `NEXT_PUBLIC_CONTACT_EMAIL`  | Browser     | Public contact address                                   |
| `NEXT_PUBLIC_TELEGRAM_URL`   | Browser     | Optional public footer link                              |
| `LEAD_WEBHOOK_URL`           | Server only | Landing early-access endpoint                            |
| `LEAD_WEBHOOK_SECRET`        | Server only | Shared early-access request secret                       |

The typed API client uses `credentials: include`, an HttpOnly JWT cookie, and an explicit CSRF
header. Only tenant routes receive `X-Organization-ID`; public and authentication routes never do.
The selected non-secret organization UUID may be stored locally, but it is validated against
`GET /api/v1/me/` before scoped queries are enabled. Switching clears all tenant query caches.

## Landing integration

The Landing design and content remain preserved. Only the centralized client URL and Login entry
point were added. Lead webhook values remain server-only; never prefix their secret with
`NEXT_PUBLIC_`.

## Known limitations

- Provider cards report real API records or honest Not connected/Planned states; no OAuth exists.
- AI Context only stores, previews, and publishes configuration versions; it does not call OpenAI.
- Inbox messages are plain user-authored content; there are no generated AI summaries or replies.
- Real provider adapters are not connected. The internal test channel is E2E/development only.
- Production invitation/password-reset email delivery is pending.
- Legal pages remain drafts requiring qualified review before launch.
