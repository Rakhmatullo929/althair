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
