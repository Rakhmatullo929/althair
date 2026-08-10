# AI runtime API

All routes are relative to `/api/v1/`, require cookie authentication and a valid
`X-Organization-ID`, and return only objects from that active membership's organization. Unsafe
requests require CSRF. Cross-tenant UUIDs return `404`; a Django superuser has no tenant bypass.

| Methods | Route | Purpose |
| --- | --- | --- |
| `GET,PATCH` | `ai/runtime-config/` | Runtime mode, provider/model, channel allow-list, debounce, output/tool and usage limits |
| `GET,PATCH` | `ai/tool-policies/` | Organization tool allow-list and automatic/approval execution mode |
| `GET` | `ai/runs/` | Paginated safe run history; filters: `conversation`, `status` |
| `GET` | `ai/runs/{run_id}/` | Safe trace metadata, draft, tool calls, and handoffs; never prompt/reasoning |
| `GET` | `ai/usage/` | Real daily/monthly token and run/outcome aggregates |
| `POST` | `conversations/{id}/ai/generate-draft/` | Idempotent manual suggest run; accepts `Idempotency-Key` |
| `POST` | `conversations/{id}/ai/pause/` | Pause AI and supersede pending work |
| `POST` | `conversations/{id}/ai/resume/` | Owner/admin resume to `suggest` or guarded `autopilot_test` |
| `GET` | `conversations/{id}/ai/runs/` | Paginated safe run history for one conversation |
| `POST` | `ai/drafts/{draft_id}/approve/` | Send the unchanged pending draft once |
| `POST` | `ai/drafts/{draft_id}/edit-and-send/` | Validate and send the supplied plain-text body once |
| `POST` | `ai/drafts/{draft_id}/reject/` | Reject with an optional bounded reason |
| `POST` | `ai/tool-calls/{tool_call_id}/approve/` | Re-authorize and execute a pending CRM mutation once |
| `POST` | `ai/tool-calls/{tool_call_id}/reject/` | Reject the proposal and create a handoff |
| `POST` | `ai/handoffs/{handoff_id}/acknowledge/` | Mark an open handoff acknowledged |
| `POST` | `ai/handoffs/{handoff_id}/assign/` | Owner/admin assignment to an active same-tenant member |
| `POST` | `ai/handoffs/{handoff_id}/resolve/` | Owner/admin resolution; resume remains a separate action |

`409` covers disabled/unpublished/paused/unsupported-channel state and stale or replayed actions;
`429` covers configured usage and manual generation limits. Error bodies provide bounded stable
`code` values and never expose provider responses, prompts, AI Context, secrets, or message bodies.

The run detail includes identifiers, status/outcome, published revision ID, prompt version/hash,
provider response/request IDs, safe token/latency counters, response language, bounded error codes,
and nested auditable actions. `input_redacted` and `output_redacted` are strictly validated tool
fields; no hidden reasoning is accepted, stored, or serialized.
