# Billing operations runbook

## Safe modes

Billing starts safely with `BILLING_ENABLE=false`. Supported providers are `fake` and `manual` only.
Fake mode is deterministic and must not power production Billing; settings fail startup if that is
attempted. Manual mode means reviewed offline billing and never claims online payment success.

For local E2E, set non-secret fake values and run:

```bash
DEBUG=true E2E_TESTING=true USE_SQLITE=1 \
  BILLING_ENABLE=true BILLING_PROVIDER=fake BILLING_DEFAULT_PLAN_KEY=starter \
  BILLING_DEFAULT_CURRENCY=UZS BILLING_TRIAL_DAYS=14 BILLING_GRACE_DAYS=7 \
  python manage.py seed_billing_demo
```

The command refuses non-debug/non-E2E execution. It creates synthetic usage, draft/open invoices,
and a failed unmapped provider event for operational UI verification. It contains no credentials or
customer content.

## Reviewed operations

Use the Internal Super Admin Billing pages. Confirm the staff identity, role, environment, and fresh
MFA before every write. Supply a specific operational reason; all results enter immutable audit.

- Publish only after validating every feature key and at least one draft price. Publication makes
  material plan/price values immutable.
- Manual subscription grants must use an active price in the billing account currency.
- Grace extensions are temporary and bounded. They do not bypass security/consent/provider controls.
- Issue only reviewed draft invoices. Void only draft/open invoices. Mark paid only after an actual
  fake event or an explicit reviewed manual confirmation.
- Reconciliation rebuilds aggregates from append-only events and is safe to repeat.

Do not paste card data, provider secrets, message bodies, prompts, transcripts, phone data, or
billing addresses into reasons, logs, provider events, or usage metadata.

## Dunning and recovery

Payment failure enters grace and creates a truthful notification record. The lifecycle task warns,
expires grace once, and restricts billable capabilities through `EntitlementService`. Login,
read-only CRM/history, Billing, support, and export remain. Payment recovery restores the active
state idempotently. No workflow deletes data, disconnects channels, or retries forever.

## Platform bootstrap and wallet operations

Run the one-off bootstrap from a single release job. Supply the first owner password through a
restricted file or standard input; it is never printed and must not be placed in shell history:

```bash
python manage.py bootstrap_platform --non-interactive --owner-email owner@example.test \
  --password-file /run/secrets/platform-owner-password --create-wallets --safe-json-report
python manage.py bootstrap_platform --check --owner-email owner@example.test
```

The command takes a PostgreSQL advisory lock, applies the stable catalog version idempotently,
creates a normal user with `is_staff=false`, `is_superuser=false`, `must_change_password=true`, and
creates only `PlatformStaffAccess(platform_owner, active, mfa_required)`. It never creates or prints
a TOTP secret. Use `--migrate-subscriptions-to-wallet` only as a separately reviewed rollout;
existing subscriptions remain manual by default. Password rotation requires the explicit
`--rotate-owner-password` switch. Adoption of an existing user requires
`--adopt-existing-owner` after identity review.

For wallet incidents, freeze first if further debits must stop, export the safe ledger, reconcile,
and retain the mismatch report. Never repair the cached balance directly. Apply a reviewed debit,
credit, or reversal with a unique idempotency key. A low-balance failure must remain a zero-debit
failure; after a verified top-up, retry only due open invoices. Escalate any reconciliation mismatch
before unfreezing.

`seed_full_demo` is deterministic and idempotent but restricted to development, staging, and the
isolated test environment. A complete safe report can be generated without printing passwords:

```bash
python manage.py seed_full_demo --organization-slug mehr-clinic \
  --with-admin --with-wallet --non-interactive --safe-json-report
```

The command reads `FULL_DEMO_SEED_PASSWORD` from the process environment (deployment tooling should
inject it from a restricted secret source) and never returns that value. A reset is allowed only
in development/staging, deletes only the exact `--organization-slug`, and requires typing that slug;
non-interactive reset additionally requires `FULL_DEMO_SEED_RESET_CONFIRMATION` to equal the slug.
Production rejects both demo seed and reset requests before reading credentials or changing data.

## Future live adapter contract

A live adapter must implement the `BillingProvider` protocol, verify signatures using its official
mechanism, map stored provider object IDs to server-owned invoices/subscriptions, compare amount and
currency, and return normalized safe results. It must not leak provider SDK types into plan,
entitlement, or customer APIs. Before enabling it, complete provider/legal selection, data-flow and
retention review, secret rotation, webhook replay testing, incident controls, tax/fiscal analysis,
and jurisdiction-specific UX review. Add it as an isolated adapter; do not alter the canonical
entitlement source.

## Verification

Run migrations/checks, the full backend suite, focused Billing coverage, frontend tests/build/E2E,
Docker config and stack health, AI/Voice evals, `scripts/check-secrets.sh`, and `git diff --check`.
Generated Billing screenshots belong under `artifacts/screenshots/` and must remain uncommitted.
