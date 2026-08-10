# AI conversation runtime architecture

The `apps.ai_runtime` module adds an auditable AI decision layer to the existing tenant-scoped CRM.
It is enabled per organization and, in this stage, accepts triggers only from the explicit internal
test channel. It does not connect any external messaging provider and it does not alter the Landing
or the underlying CRM workflow.

## Execution and provider boundary

An inbound CRM message schedules a Celery task after its database transaction commits. The task
uses the stable key `inbound:{message_id}`, locks the message and conversation, enforces one queued
or running job per conversation, then creates an `AIRun`. A configured debounce schedules that run
later; the context is rebuilt immediately before execution so a short burst is evaluated together.
Duplicate delivery returns the existing run. A human reply, pause, changed published context, or
other stale state supersedes pending work before a response or action can be used.

`AIProvider` separates orchestration from inference. `FakeAIProvider` is deterministic and is the
default for tests, E2E, and local development. `OpenAIResponsesProvider` uses the OpenAI Responses
API with `store=False`, configured timeouts/retries, strict function schemas, and `call_id`-matched
function outputs. Real calls require all of `provider=openai`,
`AI_RUNTIME_ENABLE_REAL_OPENAI=true`, a server-side `OPENAI_API_KEY`, and an explicitly configured
model. CI never needs a real key. Provider payloads and reasoning items are deliberately discarded;
the database and API contain no hidden chain-of-thought field.

## Effective prompt and context

Every run stores a prompt-template version and SHA-256 hash, not the prompt text. The prompt combines:

- platform safety rules and the requested response language;
- the latest **published** immutable `AssistantContextRevision` snapshot;
- scoped public organization/branch facts;
- the current contact, identities, open lead/tasks, and recent conversation messages;
- the bounded extractive rolling summary, when the timeline exceeds the recent-message window;
- the names of server-enabled tools.

Draft profile edits, secrets, credentials, another tenant's data, internal provider configuration,
raw logs, and arbitrary model-supplied organization IDs are excluded. Customer content is wrapped
as untrusted data. The post-generation policy rejects HTML, script-like output, unsupported booking,
payment/refund confirmations, and unsafe language selection; it creates a handoff instead.

The rolling summary is visibly labelled AI-generated, contains only shortened extracts from stored
messages, is limited to 2,000 characters, records its message range, and is refreshed from the
organization-scoped timeline. It is never presented as an independent fact source.

## Tool authorization and idempotency

The static registry exposes read tools for company, branch, contact, conversation, lead, and task
facts, plus proposed contact/CRM mutations and `request_human_handoff`. Tools are deny-by-default;
handoff is always enabled. Every provider schema is strict and disallows additional properties.

The model can only propose a tool name and arguments. The backend ignores any proposed tenant,
injects the run's organization and current conversation, validates exact argument types and UUIDs,
performs organization-scoped lookups, and re-checks the approving membership's role. Read tools may
run automatically when enabled. Mutations default to human approval, and their results are stored in
redacted form. A unique provider-call key and a run-derived idempotency key prevent replay; duplicate
approval returns a conflict and cannot repeat a write.

## AI state and handoff lifecycle

Conversation states are `off`, `suggest`, `autopilot_test`, `paused_by_human`, and
`handoff_required`. Suggest mode creates a pending `AIDraft`; an authorized member can approve it
unchanged, edit and send it, or reject it. Each action records actor and timestamps. Autopilot is
additionally guarded by a development/test server flag and can send only through the internal test
connection. The external-channel check is repeated at the final message write.

Customer requests for a person, prompt/secret exfiltration, disabled or invalid tools, safety
failures, tool limits, and rejected actions create an `AIHandoff`. A member can acknowledge it;
owners/admins can assign and resolve it. AI resumes only through an explicit owner/admin action.
Any human outbound reply immediately pauses AI and supersedes queued runs, drafts, and proposals.

## Limits, privacy, and observability

Organization settings enforce daily runs, monthly input/output tokens, maximum output size, tool
rounds, provider timeout, inbound debounce, and a per-member manual-generation throttle. Limit
checks happen before provider invocation and return stable safe codes. `AIUsageEvent` stores token
counts, cache counts, latency, outcome, provider/model name, and time buckets; it stores no message,
prompt, or context body.

Normal logs contain organization/conversation/run IDs, provider/model, status, latency, and token
counts only. Provider exceptions are converted to bounded categories/codes; raw upstream bodies,
keys, prompts, messages, and AI Context are not logged or returned. CRM activity records expose safe
human-readable events without sensitive payloads. The readiness endpoint reports only whether the
fake or explicitly enabled live adapter is configured.

The organization-scoped usage API is the metrics interface for queued/running/completed/failed and
stale runs, provider latency, handoff rate, draft approval/rejection, tool success/failure, and token
totals. Production monitoring can export those bounded aggregates without exporting content.

Retention currently follows the parent CRM/organization lifecycle; there is no separate automated
purge policy in this stage. Before production provider activation, define legal retention windows,
deletion/export jobs, and provider-side data-control policy for the deployment region.

## Evals and local workflows

`backend/evals/ai_runtime/` contains deterministic RU/UZ/EN behavior, tool-routing, prompt-injection,
tenant-boundary, and safety cases. Run `python backend/evals/ai_runtime/run_evals.py`; it has no
network dependency. Rejected/edited production bodies are never copied into this corpus—future
regression examples must be sanitized, opt-in development fixtures.

For local development, set `ENABLE_CRM_TEST_CHANNEL=true`, keep `AI_RUNTIME_PROVIDER=fake`, publish
AI Context, enable the organization runtime, and allow its internal test connection. To exercise
autopilot locally also set `AI_INTERNAL_TEST_AUTOPILOT=true`. The optional live adapter requires
`AI_RUNTIME_PROVIDER=openai`, `AI_RUNTIME_ENABLE_REAL_OPENAI=true`, `OPENAI_MODEL`, and
`OPENAI_API_KEY`; live integration tests must additionally be opt-in, use synthetic data and a strict
budget, and remain skipped by default.

## Known limitations and next adapter boundary

This stage has no public Web Chat, Instagram, Telegram, Gmail, WhatsApp, SMS, Voice, web/file search,
MCP, computer use, booking, billing, payments, refunds, or Super Admin. The only send-capable AI path
is the internal test channel. A future real-provider adapter must first authenticate the provider
event, resolve an existing tenant-owned `ChannelConnection`, call the same idempotent CRM ingestion
service, and preserve the server-side runtime, approval, handoff, and final-send checks described
above.
