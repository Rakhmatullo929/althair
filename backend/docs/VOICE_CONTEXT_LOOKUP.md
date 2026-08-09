# MMC Context Lookup API — Voice Integration Guide

**Audience:** external Voice platform team
**Status:** live · read-only · no side effects

## Purpose

Fetch a caller's already-known location context so the voice agent can **confirm
details instead of re-asking** them (site / address / city / community / builder
/ lot). This reduces repeated questions about site, address, city, township, and
community for existing customers and sites.

The API is **read-only** — it never creates or modifies any data. It reads
existing `Contact` and `JobRecord` records.

---

## Endpoint

```
GET /api/v1/intake/context-lookup/
```

Query by **one** of the following (precedence: `phone` → `site` → `community` → `address`):

| Param | Matches against | Example |
|-------|-----------------|---------|
| `phone` | caller's number (Contact) | `?phone=+15551234567` |
| `site` | `JobRecord.site` (substring) | `?site=189 East Avenue` |
| `community` | `JobRecord.community` (substring) | `?community=Maple Ridge` |
| `address` | `JobRecord.site` (substring) | `?address=200 West Street` |

---

## Auth

Static bearer token (same mechanism as the inbound webhooks). The header is
**required**:

```
Authorization: Bearer <VOICE_API_TOKEN>
```

- Missing or invalid token → `403 Forbidden`.
- The token is provisioned out-of-band (server `EXPECTED_API_TOKEN_SHA256`).

---

## Request examples

```bash
# by phone (recommended at call start)
curl -s "$BASE/api/v1/intake/context-lookup/?phone=+15551234567" \
     -H "Authorization: Bearer $VOICE_API_TOKEN"

# by site / community / address (mid-call, when the caller names a place)
curl -s "$BASE/api/v1/intake/context-lookup/?site=189%20East%20Avenue"   -H "Authorization: Bearer $VOICE_API_TOKEN"
curl -s "$BASE/api/v1/intake/context-lookup/?community=Maple%20Ridge"     -H "Authorization: Bearer $VOICE_API_TOKEN"
curl -s "$BASE/api/v1/intake/context-lookup/?address=200%20West%20Street" -H "Authorization: Bearer $VOICE_API_TOKEN"
```

Phone input is tolerant of formatting (`+1 (555) 123-4567`, `5551234567`, etc.)
— it is matched on the significant last 10 digits.

---

## Response examples

### Found — `200 OK`

```json
{
  "found": true,
  "match_type": "phone",
  "contact": { "name": "John Smith", "phone": "+15551234567" },
  "most_recent": {
    "site": "189 East Avenue, Pittsburgh, PA 15201",
    "address": "189 East Avenue",
    "city": "Pittsburgh",
    "state": "PA",
    "township": "",
    "community": "Maple Ridge",
    "builder": "DR Horton",
    "lot_phase": "Lot 42"
  },
  "known_locations": [
    {
      "site": "189 East Avenue, Pittsburgh, PA 15201",
      "address": "189 East Avenue", "city": "Pittsburgh", "state": "PA",
      "township": "", "community": "Maple Ridge", "builder": "DR Horton", "lot_phase": "Lot 42"
    },
    {
      "site": "200 West Street, Pittsburgh, PA",
      "address": "200 West Street", "city": "Pittsburgh", "state": "PA",
      "township": "", "community": "Oak Hill", "builder": "Lennar", "lot_phase": "Lot 7"
    }
  ]
}
```

### Not found — `200 OK`

```json
{
  "found": false,
  "match_type": "phone",
  "contact": { "name": "", "phone": "+19998887777" },
  "most_recent": null,
  "known_locations": []
}
```

### Errors

| Code | When |
|------|------|
| `400 Bad Request` | no query parameter provided |
| `403 Forbidden` | missing / invalid bearer token |

---

## Response schema

| Field | Type | Notes |
|-------|------|-------|
| `found` | bool | `true` when a matching contact/record was found |
| `match_type` | string | `phone` \| `site` \| `community` \| `address` \| `none` |
| `contact` | object \| null | `{ name, phone }` of the matched contact |
| `most_recent` | object \| null | best default location to confirm (newest job) |
| `known_locations` | array | **distinct** locations (by site+community), newest first |

Each location object:

| Field | Notes |
|-------|-------|
| `site` | full stored site/address string |
| `address` | street part, best-effort parsed from `site` (may be `""`) |
| `city` | best-effort parsed from `site` / contact (may be `""`) |
| `state` | 2-letter code, best-effort (may be `""`) |
| `township` | **always `""`** — not stored in MMC today |
| `community` | `JobRecord.community` |
| `builder` | `JobRecord.builder` |
| `lot_phase` | `JobRecord.project` |

---

## Recommended usage flow

```
Call starts (Caller ID known)
   └─ GET ?phone=<caller>
        ├─ found=true  → confirm `most_recent`  ("Same site at <community>?")
        │                 (use `known_locations` if the caller has multiple sites)
        └─ found=false → run normal intake (ask for site / address / community)

Caller names a site / community mid-call
   └─ GET ?site=  /  ?community=   → pull builder / lot context to confirm

After the call
   └─ POST the post-call webhook with the confirmed structured fields
      (site, community, builder, install_type, ...) so the job is created
      with the correct data.
```

**Guidance**
- Prefer **confirming** over asking whenever `found=true`.
- If `address` / `city` come back empty, fall back to confirming `community` +
  `builder` + `lot_phase`, then ask only for the missing piece.
- `township` is never returned populated — ask for it if you need it.

---

## Notes & limitations

- Phone match uses the **last 10 digits** (drops country code). For well-formed
  US numbers this is effectively unique; very short/garbage input could match
  broadly — send a full caller number.
- `address` lookups search inside `JobRecord.site` (address is stored there).
- No new database tables or fields were added for this endpoint.
