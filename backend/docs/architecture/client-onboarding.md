# Client onboarding and AI Context

## Resumable onboarding

`OrganizationProfile` stores `onboarding_current_step`, a structured completed-step list,
percentage, and completion timestamp. The aggregate onboarding endpoint returns organization,
profile, branches, and AI Context in one tenant-scoped response. A valid step save updates only
submitted fields; recoverable errors leave the client form intact. Final completion is transactional
and server-validated, so a client cannot mark an incomplete tenant ready.

The six UI steps are company identity, business description, first branch/hours, team/invitations,
AI behavior, and review. The portal resumes from the stored server step. Completion requires public
business identity, an active branch, and the core AI Context fields.

## AI Context data model

Each organization has exactly one `OrganizationAssistantProfile`. It contains plain-text business
and behavior fields, a structured supported-language list, current draft status, a monotonically
increasing published version, and an immutable copy of the latest published payload.

`AssistantContextRevision` records each publication with organization, profile, version, full
snapshot, publishing user, and timestamp. A publish transaction locks the profile, validates it,
creates the revision, and updates current publication metadata. Editing after publication changes
only the draft fields and status; the published snapshot stays intact. HTML, executable code, OpenAI
calls, generated prompts, and automatic channel activation are absent by design.

## Central role permissions

| Role | Read | Operate | Manage company/branches/AI Context | Manage team/channels | Ownership |
| --- | --- | --- | --- | --- | --- |
| Owner | Yes | Yes | Yes | Yes | Yes |
| Admin | Yes | Yes | Yes | Yes | No |
| Manager | Yes | Yes | Yes | Team no; channels no | No |
| Agent | Yes | Yes | No | No | No |
| Viewer | Yes | No | No | No | No |

`OrganizationMembership` is the only authority source. Legacy user role strings cannot grant access.
An actor cannot grant a role above their authority; only an owner can grant ownership or alter
another owner; the last active owner cannot be demoted or deactivated. All roles become read-only
when the organization is suspended or archived.
