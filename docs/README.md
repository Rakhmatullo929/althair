# Althair documentation

[Project overview](../README.md) · [Русский](../README.ru.md) · [O‘zbekcha](../README.uz.md) · [Local setup](development/local-setup.md)

This index points to documentation that exists in the current repository. Provider guides describe implementation and activation requirements; they do not imply that an external account, approval, or production connection is active.

## Product and domain architecture

- [Multi-tenancy](../backend/docs/architecture/multitenancy.md)
- [Client onboarding and AI Context](../backend/docs/architecture/client-onboarding.md)
- [CRM core](../backend/docs/architecture/crm-core.md)
- [AI conversation runtime](../backend/docs/architecture/ai-conversation-runtime.md)
- [Booking and scheduling](../backend/docs/booking.md)
- [Billing, subscriptions and organization Wallet](../backend/docs/architecture/billing-subscriptions.md)
- [Internal control plane](../backend/docs/architecture/internal-control-plane.md)

## Channels and providers

- [Public Web Chat](../backend/docs/architecture/public-web-chat.md)
- [Instagram Messaging](../backend/docs/architecture/instagram-messaging.md)
- [Telegram Managed Bots](../backend/docs/architecture/telegram-managed-bots.md)
- [Gmail integration](../backend/docs/architecture/gmail-email-integration.md)
- [SMS messaging](../backend/docs/architecture/sms-messaging.md)
- [Voice AI telephony](../backend/docs/architecture/voice-ai-telephony.md)

## Live activation guides

- [Meta Instagram App Review](../backend/docs/integrations/instagram-app-review.md)
- [Telegram operations](../backend/docs/integrations/telegram-managed-bots.md)
- [Google Gmail setup and verification](../backend/docs/integrations/google-gmail-setup.md)
- [Twilio SMS setup](../backend/docs/integrations/twilio-sms-setup.md)
- [Twilio and OpenAI Voice setup](../backend/docs/integrations/twilio-openai-voice-setup.md)

Real OpenAI Responses calls are documented in the [AI Runtime architecture](../backend/docs/architecture/ai-conversation-runtime.md). They remain opt-in and require an explicit server gate, model and server-side API key. Billing currently has fake and reviewed manual adapters only; no live payment provider guide exists yet.

## APIs and operations

- [Backend guide and API documentation map](../backend/README.md)
- [Multi-tenant API](../backend/docs/api/multitenant-api.md)
- [CRM API](../backend/docs/api/crm-api.md)
- [AI Runtime API](../backend/docs/api/ai-runtime-api.md)
- [Booking API architecture](../backend/docs/booking.md#public-booking-and-operational-visibility)
- [Billing API](../backend/docs/api/billing-api.md)
- [Internal control-plane API](../backend/docs/api/internal-control-plane-api.md)
- [Billing and Wallet runbook](../backend/docs/operations/billing-runbook.md)
- [Control-plane runbook](../backend/docs/operations/control-plane-runbook.md)

## Security

- [Public vulnerability disclosure policy](../SECURITY.md)
- [Customer authentication](../backend/docs/security/client-authentication.md)
- [Internal authentication and MFA](../backend/docs/security/internal-auth-mfa.md)
- [Required credential rotation notes](../backend/docs/security/secret-rotation-required.md)

Never copy real credentials, provider payloads, customer messages, transcripts, phone numbers, financial data, or production exports into documentation or issue reports.
