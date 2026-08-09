# MMC Escalation API — Voice Integration Guide

**Audience:** external Voice platform team
**Status:** live · additive · no schema changes

## Purpose

When the voice agent decides a **human is needed** (human handoff / callback
requested / complaint), it POSTs the call context here. MMC creates an
**escalation** that shows up in the same staff **Escalation** tab as SMS
escalations — carrying the caller **name** (if given), **transcript**, and
**summary**.

This mirrors the SMS escalation behavior: it only **adds the item to the
Escalation queue**. It does **not** change the related job's priority or status.

---

## Endpoint

```
POST /api/v1/intake/escalate/
Content-Type: application/json
```

---

## Auth

Static bearer token (same token as the inbound webhooks / Context Lookup):

```
Authorization: Bearer <VOICE_API_TOKEN>
```

- An **invalid** token is rejected (`403`).
- The endpoint is otherwise open (consistent with the inbound webhooks). Always
  send the token in production.

---

## Request body

All fields are optional — send whatever the call produced. If `reason` is blank
it defaults to `"unspecified"`.

| Field | Type | Notes |
|-------|------|-------|
| `phone` | string | caller's number (used to call back / match the contact) |
| `name` | string | caller name, **if the caller gave it** |
| `reason` | string | why it's escalated, e.g. `callback_requested`, `customer-requested-human` |
| `transcript` | string | full call transcript (shown in the Escalation item) |
| `summary` | string | short AI summary of the call |
| `notes` | string | optional extra context for staff |
| `call_sid` | string | optional — Twilio CallSid, stored for traceability |
| `related_job_id` | uuid | optional — link the escalation to an existing job |

---

## Request example

```bash
curl -s -X POST "$BASE/api/v1/intake/escalate/" \
     -H "Authorization: Bearer $VOICE_API_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "phone": "+13059995694",
       "name": "John Smith",
       "reason": "callback_requested",
       "transcript": "assistant: How can I help?\nuser: Call me back right now!",
       "summary": "Customer asked for a manager callback.",
       "call_sid": "CAxxxxxxxx"
     }'
```

---

## Response

### Success — `201 Created`

```json
{
  "status": "escalated",
  "interaction_id": "8f1ba680-6efe-4c24-b584-8b5f1fd88665"
}
```

### Errors

| Code | When |
|------|------|
| `400 Bad Request` | malformed body |
| `403 Forbidden` | invalid bearer token |

---

## How it appears in the Escalation tab

The escalation lands in the existing **Escalation** queue (same place SMS
escalations show up). Staff see:

```
Voice   ·   John Smith                              Apr 30, 2:59 PM
Reason: callback_requested
Summary: Customer asked for a manager callback.
Transcript:
  assistant: How can I help?
  user: Call me back right now!
                                                         [ Read ]
```

- Channel badge: **Voice**
- Sender: caller `name` (falls back to phone if no name)
- `Reason`, `Summary`, and `Transcript` sections render from the fields above
- It is counted in the red unread badge until a staff member marks it **Read**

No UI work is required on the MMC side — the queue already renders voice items.

---

## Recommended usage flow

```
During / at the end of a call, the voice agent decides a human is needed
   └─ POST /api/v1/intake/escalate/
        body: { phone, name?, reason, transcript, summary, call_sid? }
        → 201 { interaction_id }

Staff dashboard → Escalation tab
   └─ the item appears (Voice badge, name, reason, summary, transcript)
   └─ staff calls the customer back, then marks it "Read"
```

**Guidance**
- Send `transcript` and `summary` so staff have full context without replaying
  the call.
- Send `name` whenever the caller provided it — staff need someone to ask for on
  the callback.
- Use a clear `reason` (`callback_requested`, `customer-requested-human`,
  `complaint`, …) — it is shown verbatim in the queue.

---

## Notes & limitations

- **Adds to the queue only** — does not change the related job's priority/status.
  (Auto-escalating priority, e.g. "callback → critical", is a separate task.)
- No new database tables/fields; uses the existing `Interaction` model
  (`category = escalation`).
- Each call to this endpoint creates one escalation entry (mirrors SMS). The
  `call_sid` is stored in the payload for traceability and does not collide with
  the voice webhook's CallSid de-duplication.
- The read side (list / unread-count / acknowledge) is the existing escalation
  queue API and is unchanged.
