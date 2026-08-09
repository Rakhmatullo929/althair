# Multi-tenant architecture

The backend remains a modular Django monolith backed by one PostgreSQL database. Customer access
is row-scoped by a non-null `organization_id`; `OrganizationMembership` is the only company-level
authorization source. The legacy `User.organization` and `User.role` strings remain temporarily as
deprecated migration inputs and must not be used by new permissions or serializers.

Authenticated tenant endpoints require `X-Organization-ID`. Missing or invalid identifiers return
`400`, inactive membership returns `403`, and object identifiers from another tenant return `404`.
Normal customer endpoints do not grant a superuser an implicit tenant bypass.

Inbound provider events resolve an active `ChannelConnection` using verified destination routing
data. Sender identity and message content never select a tenant. Unknown or inactive destinations
fail closed. Provider message idempotency is scoped by organization and connection.

AI tools receive tenant-owned objects from server-side context. Model validation rejects related
objects owned by another organization, and credentials remain encrypted/write-only.

## Legacy MMC vertical

The MMC job fields and PO/REQ rules are intentionally preserved in `intake.JobRecord` during this
migration. They execute only on an already tenant-scoped job. A later vertical-module refactor
should move this policy behind an explicit organization industry/module boundary without deleting
or reinterpreting migrated data.
