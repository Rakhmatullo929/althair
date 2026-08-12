# Twilio SMS setup and operations

## Deterministic local workflow

Use the checked-in empty-value examples and keep actual values in an ignored local `.env` or secret
manager. The default local mode is:

```text
SMS_PROVIDER=fake
SMS_ENABLE_LIVE=false
SMS_FAKE_PROVIDER=true
TWILIO_PUBLIC_BASE_URL=http://localhost:8000
```

Start the Docker stack, sign in as an owner/admin, open **Channels → SMS**, create a deterministic
fake connection, and use **Test inbound**. No Twilio credential or internet request is required.
The fake sender returns stable provider SIDs for the same connection, recipient, text, and callback;
recipients ending in `0000` exercise a permanent failure. Playwright uses this mode.

## Optional live sandbox checklist

Use only synthetic test recipients and a Twilio test subaccount. Before setting
`SMS_ENABLE_LIVE=true`:

1. Choose platform-managed deployment credentials or customer-owned credentials. Prefer an API key
   for REST calls and retain the Auth Token only where Twilio webhook validation requires it.
2. Configure an SMS-capable E.164 number or Messaging Service and record its provider SID.
3. Publish the backend at a stable HTTPS origin and set `TWILIO_PUBLIC_BASE_URL` to that exact
   externally visible origin. Do not include a path or credentials.
4. Configure the displayed inbound and status URLs in Twilio exactly, including the opaque path and
   trailing slash. Proxies must not rewrite the path/query before Django validates it.
5. Enable Advanced Opt-Out in Twilio and the portal together when using provider-managed keyword
   handling. Verify STOP, START, and HELP with a synthetic recipient.
6. Run provider health, then inbound, duplicate replay, manual send, delivered/failed callback,
   Unicode segmentation, opt-out block, and disconnect tests.
7. Configure explicit country allow/deny policy and conservative product limits. These are Althair
   safety limits, not claims about a Twilio/carrier quota.
8. Confirm applicable A2P registration, sender authorization, consent evidence, quiet-hours,
   disclosure, retention, and emergency/escalation obligations with local counsel and carriers.

Do not enable live mode merely because the portal checklist is green; it reports technical
readiness, not carrier or legal approval.

## Signature and proxy troubleshooting

- `invalid_signature`: compare Twilio's requested URL with the portal URL byte-for-byte. Check HTTPS
  termination, host, port, path, query, trailing slash, and that the proxy has not decoded/reordered
  form values. Never validate only a hand-picked parameter subset.
- `public_https_required`: configure a stable HTTPS `TWILIO_PUBLIC_BASE_URL`; localhost HTTP is only
  for the fake provider.
- `destination_mismatch`: the signed `To` or Messaging Service SID is not the sender attached to the
  opaque connection key. Tenant headers cannot repair this.
- JSON validation failure: Twilio JSON webhooks must include its `bodySHA256` query parameter and the
  raw body must arrive unchanged.
- `credentials_missing`, `provider_unreachable`, or a provider number code: rotate the write-only
  customer credential or fix deployment secrets, then run health. Normal logs expose only the safe
  code.

## Consent, delivery, and incident runbook

- STOP is authoritative immediately. Confirm the conversation composer is blocked and neither
  manual nor AI sends call the provider. With Advanced Opt-Out, only a verified provider START can
  restore sending. HELP stays support-only and never triggers AI.
- For a delivery failure, inspect the non-secret provider code, recipient/country policy, consent,
  sender health, failed-attempt count, and circuit state. Permanent number/opt-out errors are never
  automatically retried. An owner/admin may request one of the remaining bounded attempts.
- For webhook dead letters, repair the safe configuration error and let the bounded task sweep
  requeue receipts from the last 24 hours. The stored inbound body is redacted after success.
- If abuse or SMS pumping is suspected, pause the connection, narrow country policy, lower rate and
  daily limits, review tenant audit/count data, and use Twilio's own fraud tooling where available.
  The local fraud interface deliberately does not invent provider risk data.
- To roll back, pause then disconnect the connection, remove Twilio webhook URLs and revoke/rotate
  credentials. CRM history remains tenant-owned; apply the documented privacy/retention workflow.

## Known limitations and next stage

This stage accepts MMS count metadata but does not download media. Sender discovery is validated at
connection health rather than treated as a carrier inventory system. Country allow/deny policy uses
phone-number region metadata and cannot guarantee current reachability, pricing, premium status,
registration, or carrier filtering. SMS has no ordinary read receipt.

Voice is intentionally not present. A separate Prompt #10 should design tenant-owned telephony,
signed voice webhooks, call consent, realtime media boundaries, recording/transcription policy,
human transfer, emergency safeguards, and Voice AI evaluation without coupling those risks to SMS.
