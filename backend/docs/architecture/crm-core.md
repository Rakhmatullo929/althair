# CRM core architecture

The CRM is an `apps.crm` module inside the Django monolith. It supplies a real, provider-independent
customer workflow while preserving the existing authentication, onboarding, AI Context, and legacy
modules. It does not call OpenAI or claim that any external channel is connected.

## Tenant ownership and permissions

Every CRM aggregate has a non-null `organization_id`. Request scope comes exclusively from an
active `OrganizationMembership` and `X-Organization-ID`; request bodies, contact identities, and
sender metadata never select the tenant. UUID lookups are performed against an organization-filtered
queryset, so another tenant's object returns `404`. A Django superuser has no implicit bypass.

Safe reads are available to active members. Owners and admins can create, edit, merge, assign, and
manage the development channel. Agents can work conversations, contacts, leads, and tasks but
cannot merge contacts or use the internal test tool. Viewers are read-only. Suspended or archived
organizations remain readable but all CRM mutations are rejected centrally.

## Contacts and identities

`Contact` owns normalized channel identities through `ContactIdentity`. Normalization is
type-specific: email is case-folded, phone-like values are reduced to a leading plus and digits,
and provider handles are trimmed and case-folded. A database constraint prevents two active
contacts in one organization from owning the same normalized identity for a type and connection.
Duplicate suggestions are honest matches derived from those normalized identities.

Merge is atomic and explicit. Identities, tags, notes, conversations, leads, and tasks are moved to
the target; conflicting identities are de-duplicated; the source is archived as a tombstone with
`merged_into`; and an immutable activity record identifies actor, source, and target. No records
may cross an organization boundary.

## Conversations, messages, and handoff

Provider-independent ingestion resolves an organization-owned active `ChannelConnection`, then a
normalized identity, contact, and open conversation. It never trusts sender content for tenant
routing. A provider message ID is unique within organization and connection; the idempotency check
occurs before unread counters, timestamps, or activity records are changed.

Messages are stored as plain text with a direction and delivery status. HTML is rejected and the
client linkifies only safe URL schemes. Internal notes are system-direction messages, are visibly
distinguished, and are never eligible for a future provider adapter. Conversation state includes
open, pending, resolved, and closed; assignment; priority; unread count; automation enabled/paused;
and an explicit human-handoff state. Replying to a resolved conversation reopens it. Reading clears
unread state for that organization only.

## Leads, pipelines, and follow-up tasks

Each organization receives a deterministic default pipeline and ordered stages. Leads link to a
tenant-owned contact and optionally to a conversation. Stage changes are validated against the
lead's pipeline and logged. Marking won or lost is explicit, with a required lost reason; moving a
terminal lead back to an active stage clears terminal state instead of retaining misleading sales
data. The UI never invents sales value or revenue.

Follow-up tasks link to organization-owned contacts and optional leads/conversations. Owners,
admins, and agents can create, complete, or cancel them; timestamps and activity are stored from
real writes. Overview metrics and stage counts are database aggregates, not placeholders.

## Internal development channel

`ENABLE_CRM_TEST_CHANNEL` defaults to false. When explicitly true in a development/E2E backend,
owners and admins can create a deterministic inquiry and send a simulated outbound message through
the same persistence services used by future adapters. This is labelled as an internal test channel,
requires no credentials, and does not report a real provider connection. `seed_crm` refuses unsafe
production use and creates repeatable local fixtures.

## Future provider boundary and limitations

Future adapters should verify provider signatures and destination routing, resolve an existing
tenant-owned `ChannelConnection`, then call the ingestion/send services. They must preserve the
idempotency key, apply delivery receipts through `record_delivery_update`, and never perform CRM
writes before tenant resolution. Instagram, Telegram, Gmail,
WhatsApp, SMS, and Voice are not connected. OpenAI summaries, reply generation, booking, billing,
and Internal Super Admin remain outside this stage.
