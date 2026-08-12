# Google Gmail setup and verification

1. Create a Google OAuth web application and configure the callback exactly as
   `GOOGLE_GMAIL_REDIRECT_URI`.
2. Request only `https://www.googleapis.com/auth/gmail.modify`. Complete Google's OAuth consent and
   restricted-scope verification requirements before live use.
3. Create `GOOGLE_GMAIL_PUBSUB_TOPIC` in the same Google Cloud project and grant Gmail's push
   publisher service account permission to publish.
4. Create a push subscription targeting `/api/v1/webhooks/google/gmail-pubsub/`. Enable authenticated push
   with the dedicated `GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT` and exact
   `GOOGLE_GMAIL_PUBSUB_AUDIENCE`.
5. Set the full subscription resource name in `GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION`. The backend
   validates it on every envelope.
6. Enable `GOOGLE_GMAIL_ENABLE_LIVE=true` only after `/health/ready` reports Gmail configured.

Use separate Google Cloud projects and credentials for development/test and production. Tests use
the deterministic fake provider and must not contact Google. The live provider remains fail-closed
when any client, redirect, topic, subscription, audience or service-account setting is absent.

## Scope and verification checklist

The exact requested scope is `https://www.googleapis.com/auth/gmail.modify`. `gmail.metadata` cannot
read customer bodies, `gmail.send` cannot ingest or reconcile incoming threads, and `gmail.readonly`
cannot provide the implemented watch/label modification workflow. No permanent-delete or
domain-wide delegation capability exists.

Before production OAuth publication, prepare and independently verify:

- a verified product domain and accurate public homepage;
- a linked privacy policy and Google user-data/AI-processing disclosure;
- Search Console domain ownership;
- the exact scope and written justification, including why narrower scopes fail;
- an end-to-end consent, ingestion, reply, disconnect and data-control demo video;
- export, anonymization/local deletion and retention instructions;
- Google branding compliance;
- separate development and production projects;
- the applicable restricted-scope security assessment and annual review owner;
- synthetic test-user instructions.

The in-product checklist is evidence tracking, not a statement that verification, assessment or
approval has been granted. Privacy/legal copy is a draft until qualified legal review.

## Runtime and retention settings

Safe environment examples are documented in `.env.example`. Important controls include live/fake
gates, exact OAuth/Pub/Sub resources, initial-sync days/count, incremental/full bounds, daily watch
renewal threshold, send/message/attachment limits and retention batch size. Secret values belong in
the deployment secret manager, never source, frontend variables, URLs, screenshots or logs.

Operational verification:

- Connect a test Workspace mailbox and verify the refresh token is shown only as present.
- Send a new inbound email and confirm one Pub/Sub envelope, sync run, CRM email identity,
  conversation and message.
- Replay the same Pub/Sub message ID and Gmail message ID; no duplicate CRM message is created.
- Expire the history cursor and confirm a bounded full sync.
- Verify daily watch renewal and reconciliation tasks run.
- Reply from Unified Inbox and inspect the Gmail thread, `In-Reply-To` and `References` behavior.
- Confirm automated/list/bounce mail never triggers AI and a manual reply pauses AI.
- Revoke Google access and verify the UI reports reauthorization without exposing provider payloads.

Also verify reconnect with an OAuth response that omits `refresh_token`, `from_now` and bounded
recent initial modes, cancel/retry, Cc and Reply-To behavior, HTML/charset parsing, attachment
allowlist/signature rejection, privacy export/anonymize, retention cleanup, and RU/UZ/EN mobile UI.

## Optional live sandbox

Live sandbox checks are skipped by default. If explicitly enabled, use a synthetic mailbox and
synthetic correspondents only. Confirm watch creation, authenticated push, paginated history,
threaded reply and reconnect; remove the test connection and revoke the grant afterward. Never use
employee or customer mail for a test run.

## Troubleshooting and known limitations

- `configuration_incomplete`: compare readiness output with all OAuth and Pub/Sub environment keys.
- `pubsub_identity_invalid`: verify the push audience, issuer and dedicated service-account email.
- `pubsub_subscription_mismatch`: provide the full expected subscription resource name.
- `reauth_required` or `permission_missing`: reconnect the same mailbox and grant exact
  `gmail.modify`; a different mailbox is rejected.
- `watch_expired`: renew watch immediately; reconciliation closes notification gaps.
- `history_cursor_expired`: expected bounded full-resync fallback after Gmail 404.
- `attachment_content_invalid`: file bytes do not match the allowlisted declared type.

This stage has no generic IMAP/SMTP, Outlook, campaigns/bulk mail, Gmail filter management,
permanent Gmail deletion, domain-wide delegation or production deployment. Attachments are
allowlisted and signature-checked but no production malware-scanning/object-storage adapter is
bundled. Gmail's UI link is emitted only for a safely validated thread ID.
