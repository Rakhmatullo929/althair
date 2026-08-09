# AI Front Office frontend

Production-oriented public landing and shared frontend foundation for a pre-launch, multi-tenant AI Front Office product. The project deliberately does not claim that planned integrations or product capabilities are publicly available.

## Architecture

```text
frontend/
├── apps/
│   └── landing/                 Next.js App Router application
│       ├── src/app/             localized pages, metadata routes, API route
│       ├── src/components/      landing, demos, form, header, footer
│       ├── src/i18n/            next-intl routing and request setup
│       ├── src/messages/        ru, uz, and en dictionaries
│       ├── src/lib/             shared client/server Zod schema
│       └── tests/               Playwright smoke, a11y, and screenshots
├── packages/
│   ├── brand/                   typed brand config and replaceable SVG mark
│   ├── ui/                      reusable UI primitives
│   ├── eslint-config/           shared ESLint flat config
│   └── typescript-config/       shared strict TypeScript config
├── artifacts/screenshots/       generated visual QA artifacts (ignored by Git)
├── pnpm-workspace.yaml
└── turbo.json
```

Only the landing application exists. Future CRM and Super Admin apps should be added as separate workspace packages, not embedded in the landing app.

## Prerequisites and commands

- Node.js 24 Active LTS (`nvm use` reads `.nvmrc`)
- Corepack/pnpm 11

```bash
corepack enable
pnpm install
pnpm dev
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
pnpm --filter @workspace/landing exec playwright install chromium
pnpm test:e2e
pnpm screenshots
```

The site is available at `http://localhost:3000/ru`; `/uz` and `/en` are the other localized routes.

## Environment variables

Copy `.env.example` to `.env.local` for local configuration. Never commit the result.

| Variable                       | Exposure    | Purpose                                 |
| ------------------------------ | ----------- | --------------------------------------- |
| `NEXT_PUBLIC_BRAND_NAME`       | Browser     | Replaceable product wordmark            |
| `NEXT_PUBLIC_BRAND_SHORT_NAME` | Browser     | Short display name                      |
| `NEXT_PUBLIC_CONTACT_EMAIL`    | Browser     | Footer and legal contact                |
| `NEXT_PUBLIC_TELEGRAM_URL`     | Browser     | Optional footer link                    |
| `NEXT_PUBLIC_APP_URL`          | Browser     | Production canonical base URL           |
| `LEAD_WEBHOOK_URL`             | Server only | Validated early-access destination      |
| `LEAD_WEBHOOK_SECRET`          | Server only | Optional `x-lead-webhook-secret` header |

Without `LEAD_WEBHOOK_URL`, the API returns a structured `DEMO_MODE` error and does not claim to store the lead. In development it logs only a sanitized summary. The server route includes schema validation, normalization, a honeypot, minimum-fill-time protection, and basic in-memory IP throttling. Replace the webhook request in `src/app/api/early-access/route.ts` with a Django API call when the backend is ready; the form and shared schema can remain unchanged. Use durable, infrastructure-level rate limiting before a large public launch.

## Brand and visual system

- Change names, links, and contact defaults in `packages/brand/src/index.ts` or through public environment variables.
- Replace the original temporary logo once in `packages/brand/src/mark.tsx`; `Logo`, favicon, wordmark surfaces, and hero usage share this foundation. Update `apps/landing/src/app/icon.svg` and the Open Graph mark at the same time when a final logo is approved.
- Change design tokens in `apps/landing/src/app/globals.css`. Emerald, typography, spacing, focus, and motion-reduction rules live there.
- Reusable primitives are exported from `packages/ui`: `Button`, `Badge`, `Card`, `Container`, `Section`, `SectionHeading`, `Logo`, `LogoMark`, `Wordmark`, `IconTile`, `Dialog`, `MobileNavigation`, `LanguageSwitcher`, `Accordion`, and `Field`.

## Content, locales, and capability status

Visible landing copy lives in `apps/landing/src/messages/{ru,uz,en}.json`. Keep the dictionaries structurally aligned. Locale-prefixed routing and default Russian behavior are configured in `src/i18n/routing.ts`.

Channel cards are data-driven in each dictionary. Their `status` must be one of `planned`, `beta`, or `available`. Do not switch a channel to `available` until product ownership confirms that its integration is actually public.

Metadata, canonical URLs, hreflang links, Open Graph/Twitter data, sitemap, robots, app icon, and truthful SoftwareApplication JSON-LD are implemented. Set `NEXT_PUBLIC_APP_URL` to the exact public origin before deployment.

## Future applications

Add `apps/client-crm` and `apps/super-admin` as their own Next.js packages, then depend on `@workspace/ui`, `@workspace/brand`, and the shared configs with `workspace:*`. Add package-specific Turbo tasks only when they differ from the existing `build`, `dev`, `lint`, and `typecheck` pipeline. Do not copy design tokens into each app; move the global token stylesheet into `packages/ui` when the second app begins.

## Deployment

Deploy `apps/landing` from this workspace with Node 24 and pnpm. On Vercel, set the root directory to `frontend`, use `pnpm build`, and configure the public URL and server-only webhook values. Any Node-compatible host can run `pnpm --filter @workspace/landing start` after the workspace build.

## Pre-launch limitations

- All communication integrations, AI execution, CRM behavior, authentication, payments, and onboarding are intentionally not implemented.
- Scenario and CRM interfaces use typed static demo data only.
- The privacy and terms pages are clearly marked drafts. **Qualified legal review is required before public launch.**
- No pricing, trials, setup times, testimonials, metrics, certifications, or public-availability claims have been invented.
- The temporary in-memory rate limiter is per process and must be replaced by durable shared infrastructure before meaningful public traffic.
