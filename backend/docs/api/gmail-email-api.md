# Gmail email API

All tenant routes require authenticated cookies and an exact `X-Organization-ID` membership.
Owner/admin permission is required for channel mutations. Credential values are never returned.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/integrations/gmail/readiness/` | Safe live/fake readiness and exact requested scope |
| GET | `/api/v1/integrations/gmail/` | List organization Gmail mailboxes |
| GET | `/api/v1/integrations/gmail/oauth/start/` | Create single-use state and Google authorization URL |
| GET | `/api/v1/integrations/gmail/oauth/callback/` | Exchange server-side code and create connection |
| GET/PATCH | `/api/v1/integrations/gmail/{id}/` | Health, sync history and automation mode |
| POST | `/api/v1/integrations/gmail/{id}/reconnect/` | Start mailbox-bound OAuth reconnect |
| POST | `/api/v1/integrations/gmail/{id}/health/` | Live/fake provider health check |
| POST | `/api/v1/integrations/gmail/{id}/watch/renew/` | Renew `users.watch` |
| POST | `/api/v1/integrations/gmail/{id}/resync/` | Bounded recent full synchronization |
| POST | `/api/v1/integrations/gmail/{id}/sync/cancel/` | Cancel a running initial synchronization |
| GET/POST | `/api/v1/integrations/gmail/{id}/privacy/` | Export or confirmed anonymize/delete local CRM Gmail data |
| POST | `/api/v1/integrations/gmail/{id}/disconnect/` | Stop watch and clear tokens |
| GET | `/api/v1/integrations/gmail/attachments/{record}/{index}/` | Tenant-authorized bounded attachment download |
| POST | `/api/v1/webhooks/gmail/pubsub/` | Google OIDC-authenticated Pub/Sub push |
| POST | `/api/v1/webhooks/google/gmail-pubsub/` | Canonical alias for the same authenticated push handler |

OAuth start accepts `initial_sync_mode=recent|from_now` and a bounded
`initial_sync_max_messages`. PATCH accepts `automation_mode`, the initial limit, safe label lists,
and `retention_days`; model validation always keeps `SPAM` and `TRASH` excluded. Reconnect binds the
state to the existing connection and rejects a different mailbox. If Google omits a new refresh
token, the prior encrypted refresh token is retained.

Privacy export requires `contact_id`. A mutation additionally requires
`{"confirm": true, "mode": "anonymize"|"delete"}`. This deletes or redacts local CRM copies; it
does not request Gmail permanent deletion and never needs the broad `mail.google.com` scope.

The fake-only `test-inbound` and `test-state` endpoints exist only while the deterministic provider
is explicitly enabled in debug/test/E2E. `defer_sync=true` can seed a fake message for an
authenticated Pub/Sub E2E path. Production compose forces the fake provider off.

Safe error codes include `oauth_state_invalid`, `oauth_state_replayed`,
`gmail_mailbox_already_connected`, `pubsub_identity_invalid`,
`pubsub_subscription_mismatch`, `history_cursor_expired`, `reauthorization_required`,
`thread_context_missing`, `watch_expired`, `initial_sync_running`, `attachment_too_large`,
`attachment_type_not_allowed`, and `attachment_content_invalid`. Raw Google error bodies are not
returned.
