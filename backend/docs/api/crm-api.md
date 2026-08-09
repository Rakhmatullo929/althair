# CRM API

All routes are relative to `/api/v1/`, require cookie authentication, and require
`X-Organization-ID`. Unsafe requests also require the CSRF header. List routes support bounded
pagination; conversation/message lists use stable cursor pagination. Search and message writes have
dedicated throttle scopes.

| Methods | Route | Purpose |
| --- | --- | --- |
| `GET,POST` | `contacts/` | Search/list or create a contact |
| `GET,PATCH` | `contacts/{id}/` | Contact detail, edit, or archive by status |
| `POST` | `contacts/{id}/merge/` | Atomic owner/admin/manager contact merge |
| `GET,POST` | `contacts/{id}/identities/` | List or add normalized identities |
| `PATCH,DELETE` | `contacts/{id}/identities/{identity_id}/` | Edit or remove an identity |
| `GET,POST` | `contacts/{id}/notes/` | Contact notes |
| `GET,POST` | `tags/` | Organization tags |
| `GET` | `conversations/` | Filter/search the Unified Inbox |
| `GET,PATCH` | `conversations/{id}/` | Conversation detail/state |
| `GET,POST` | `conversations/{id}/messages/` | Cursor timeline / internal-channel reply |
| `POST` | `conversations/{id}/notes/` | Internal note, never provider-bound |
| `POST` | `conversations/{id}/mark-read/` | Clear unread state |
| `POST` | `conversations/{id}/assign/` | Assign/unassign and hand off |
| `POST` | `conversations/{id}/resolve/` or `reopen/` | Audited state transition |
| `GET,POST` | `pipelines/` | Pipelines and ordered stages |
| `GET,PATCH` | `pipelines/{id}/` | Pipeline detail/edit |
| `GET,POST` | `pipelines/{id}/stages/` | Ordered stages |
| `PATCH` | `pipeline-stages/{id}/` | Rename/reorder a stage |
| `GET,POST` | `leads/` | List/filter or create leads |
| `GET,PATCH` | `leads/{id}/` | Lead detail/edit |
| `POST` | `leads/{id}/move/` | Validated stage transition |
| `POST` | `leads/{id}/win/` or `lose/` | Explicit terminal outcome |
| `GET,POST` | `follow-up-tasks/` | List/filter or create follow-ups |
| `GET,PATCH` | `follow-up-tasks/{id}/` | Edit, complete, or cancel a task |
| `GET` | `crm/activity/` | Immutable organization activity feed |
| `GET` | `crm/overview/` | Real CRM aggregates and readiness |
| `POST` | `crm/dev/test-conversations/` | Owner/admin development inquiry; flag-gated |

Filters include conversation status, priority, assignment, unread, channel, and free-text search;
lead pipeline/stage/status; and task status/due bucket/assignment. All related UUIDs are resolved
inside the selected tenant. IDs from another tenant return `404`; unauthorized mutations return
`403`; merge/duplicate conflicts return `409`; throttles return `429`.

Inbound provider IDs are unique per organization and connection. Replaying the same ID returns the
original message without incrementing unread counts, changing conversation timestamps, or adding
activity. The internal test channel is unavailable unless `ENABLE_CRM_TEST_CHANNEL=true` and is
never enabled by browser state.
