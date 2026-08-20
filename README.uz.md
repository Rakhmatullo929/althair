<div align="center">

[English](README.md) · [Русский](README.ru.md) · [O‘zbekcha](README.uz.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/readme/hero-light.svg">
  <img alt="Althair AI — zamonaviy biznes uchun AI Front Office va CRM" src="docs/assets/readme/hero-light.svg" width="100%">
</picture>

**Althair AI mijozlar murojaatlari, CRM, boshqariladigan AI avtomatizatsiyasi, bronlash, jamoa va billingni bitta tenant-himoyalangan ish maydonida birlashtiradi.**

[Ishlayotgan mahsulot](https://www.althair-ai.com/) · [3 daqiqalik demo](https://www.althair-ai.com/video) · [Hujjatlar](docs/README.md)

<p>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-174c3c?style=flat-square">
  <img alt="Django 5.2" src="https://img.shields.io/badge/Django-5.2-0b6b47?style=flat-square">
  <img alt="Node.js 24+" src="https://img.shields.io/badge/Node.js-24%2B-174c3c?style=flat-square">
  <img alt="Next.js 16.3" src="https://img.shields.io/badge/Next.js-16.3-0b6b47?style=flat-square">
  <img alt="PostgreSQL 16" src="https://img.shields.io/badge/PostgreSQL-16-174c3c?style=flat-square">
  <img alt="Redis 7 and Celery 5.6" src="https://img.shields.io/badge/Redis_7_%2B_Celery-5.6-0b6b47?style=flat-square">
  <img alt="pnpm 11.21" src="https://img.shields.io/badge/pnpm-11.21-174c3c?style=flat-square">
  <img alt="Languages RU, UZ and EN" src="https://img.shields.io/badge/Languages-RU_·_UZ_·_EN-0b6b47?style=flat-square">
</p>

</div>

## Althair nimalar qiladi

| | |
| --- | --- |
| **Yagona Inbox**<br>Web Chat, Instagram, Telegram, Gmail, SMS va kiruvchi Voice qo‘ng‘iroqlarini bitta mijoz tarixida jamlaydi. | **CRM**<br>Suhbatlarni kontakt, lead, keyingi vazifa, mas’ul xodim va tekshiriladigan harakatlar tarixiga aylantiradi. |
| **Xavfsiz AI Runtime**<br>E’lon qilingan biznes kontekstiga tayangan draft va amallarni server siyosati, tasdiqlash hamda insonga topshirish bilan boshqaradi. | **Bronlash va jadval**<br>Xodimlar, ommaviy bronlash, Inbox, AI va Voice uchun bir xil haqiqiy bo‘sh vaqtni ishlatadi; soxta tasdiq bermaydi. |
| **Omnikanal aloqa**<br>Kanal ulanishlarini tashkilotga tegishli, webhook’larni tekshirilgan, amallarni idempotent, consent va delivery statuslarini aniq qiladi. | **Billing va kompaniya Wallet’i**<br>Versiyalangan tariflar, entitlement, hisoblar, foydalanish va o‘zgarmas, manfiy bo‘lmaydigan tashkilot ledger’ini yuritadi. |

## Mahsulot bo‘ylab qisqa tur

![Althair Client Portal’ning to‘rtta ko‘rinishi: Yagona Inbox, Bronlash, AI Automation va kompaniya Wallet’i](docs/assets/readme/product-tour.webp)

Statik to‘rt panelli kollaj haqiqiy interfeys matnini GitHub’da o‘qiladigan saqlaydi va juda kichik asset bo‘lib qoladi. Ko‘rinadigan barcha tashkilotlar, odamlar, uchrashuvlar va balanslar deterministik demo ma’lumotidir.

[![Althair AI 3 daqiqalik demosini ko‘rish](docs/assets/readme/demo-cover.webp)](https://www.althair-ai.com/video)

## Murojaatdan natijagacha

```mermaid
flowchart TD
    Customer[Mijoz] --> Channels[Web Chat · Instagram · Telegram · Gmail · SMS · Voice]
    Channels --> Connection[Tashkilotga tegishli tekshirilgan ChannelConnection]
    Connection --> Inbox[Yagona Inbox va CRM]
    Inbox --> Context[Oxirgi e’lon qilingan AI Context]
    Context --> Runtime[Boshqariladigan AI Runtime]
    Runtime --> Tools[Server ruxsat bergan vositalar]
    Tools --> Outcomes[Lead · Vazifa · Bron · Insonga topshirish]
    Outcomes --> Employee[Xodim]
    Employee --> Inbox
    Guard[Billing va entitlement] -. cheklov .-> Inbox
    Guard -. cheklov .-> Runtime
```

Tenant mijoz matni biznes mantiqiga yetmasidan oldin aniqlanadi: kiruvchi hodisa faol, tashkilotga tegishli ulanish orqali topilishi shart. Billing va entitlement — model tanlaydigan amal emas, backend’dagi qat’iy cheklovdir.

## Platforma arxitekturasi

```mermaid
flowchart TB
    subgraph Frontend[Mahsulot interfeyslari]
        Landing[Ko‘p tilli Landing]
        Client[Client Portal]
        Admin[Internal Super Admin]
        Public[Web Chat widget va Public Booking]
    end

    Events[Imzolangan provider hodisalari] --> Connection[Faol ChannelConnection]
    Connection --> Boundary[organization_id + OrganizationMembership]

    subgraph Backend[Django 5.2 modulli monoliti]
        Boundary --> CRM[CRM va Yagona Inbox]
        Boundary --> AI[AI Runtime va e’lon qilingan Context]
        Boundary --> Booking[Bronlash va eslatmalar]
        Boundary --> Billing[Billing va Wallet]
        Boundary --> Providers[Provider adapterlari]
        Control[Alohida control-plane auth, rollar, MFA va audit]
    end

    Client --> Backend
    Public --> Backend
    Admin --> Control
    Landing --> Public
    Providers --> Connection
    Backend --> PostgreSQL[(PostgreSQL 16)]
    Backend --> Redis[(Redis 7)]
    Redis --> Celery[Celery workers]
    Redis --> Voice[Voice gateway worker]
    Backend --> Storage[S3-compatible yoki deployment storage]
```

Customer API’lar hatto Django superuser uchun ham tashkilot doirasida qoladi. Ichki platforma rollari alohida autentifikatsiya domenidan foydalanadi va customer API’ni chetlab o‘tish huquqini bermaydi.

## Nima tayyor, nimalar esa hali faollashtirilishi kerak

**Belgilash:** ✅ repository’da tayyor · 🧪 deterministik fake/no-network yo‘li · ⚙️ deployment sozlamasi · 🔐 credential, review yoki tashqi tasdiq

| Imkoniyat | Repository holati | Live ishga tushirish talabi |
| --- | --- | --- |
| Public Web Chat | ✅ 🧪 Tenant-owned widget, sessiyalar, CRM ingestion, SSE/polling | ⚙️ Public enable flag, ruxsat etilgan origin va widget URL; live AI uchun provider ham sozlanadi |
| Instagram | ✅ 🧪 Messaging, OAuth/webhook chegarasi, javoblar va health | 🔐 Meta app credential’lari, permissions va zarur bo‘lsa App Review/Advanced Access |
| Telegram | ✅ 🧪 Managed botlar, opaque imzolangan webhook’lar, javoblar va health | 🔐 Manager-bot credential’lari va ommaviy HTTPS webhook sozlamasi |
| Gmail | ✅ 🧪 OAuth, Pub/Sub ingestion, cheklangan sync va javoblar | 🔐 Google OAuth verification, Pub/Sub resurslari va zarur security assessment |
| SMS | ✅ 🧪 Twilio SDK signature verification, STOP/START/HELP, delivery callbacks | 🔐 Twilio credential’lari, raqam/Messaging Service, operator va mahalliy consent talablari |
| Voice | ✅ 🧪 Kiruvchi Voice AI, Realtime controller, consent va xavfsiz tools | 🔐 Twilio/OpenAI credential’lari, SIP/public HTTPS va cheklangan live interoperability tekshiruvi |
| CRM va Inbox | ✅ Asosiy domen | Ichki deterministik kanal uchun tashqi provider shart emas |
| AI Runtime | ✅ 🧪 Fake — xavfsiz default; OpenAI Responses adapteri mavjud | 🔐 Aniq live gate, server API key, model, limitlar va e’lon qilingan AI Context |
| Booking | ✅ 🧪 Umumiy availability, hold, public booking, reminder, AI/Voice tools | ⚙️ Booking’ni yoqish; live reminder sozlangan consent-aware kanalni ishlatadi |
| Billing | ✅ 🧪 Provider-independent subscription, usage va invoice | ⚙️ Faqat fake/manual; **live payment gateway, karta yig‘ish, tax yoki fiscalization yo‘q** |
| Company Wallet | ✅ O‘zgarmas tenant ledger va atomik invoice debit | ⚙️ Catalog/wallet policy bootstrap’i; customer balansni ko‘radi, o‘zgartira olmaydi |
| Internal Super Admin | ✅ Alohida app, session, role, MFA va audit | ⚙️ Alohida admin origin, control plane enablement va haqiqiy MFA; fake MFA faqat dev/test uchun |

Ommaviy Landing va [`/video`](https://www.althair-ai.com/video) 2026-yil 21-avgust kuni live holatda tekshirildi. Jadval Client/Admin, tashqi provider, OpenAI account yoki to‘lov tizimi production’da faol deb **da’vo qilmaydi**.

## Xavfsiz harakat qiladigan AI

1. Runtime faqat eng so‘nggi **e’lon qilingan**, o‘zgarmas AI Context va joriy tenant’ga tegishli CRM faktlarini oladi; draft va credential kiritilmaydi.
2. Mijoz xabari ishonchsiz ma’lumot hisoblanadi. Model qat’iy tool call taklif qiladi, ammo tenant tanlay olmaydi va provider’ni to‘g‘ridan-to‘g‘ri chaqirmaydi.
3. Backend organization scope’ni qo‘shadi, rol va siyosatni qayta tekshiradi, argumentlarni validatsiya qiladi, so‘ng approval va idempotency qo‘llaydi.
4. Xodim javobi AI’ni to‘xtatadi va eskirgan ishni bekor qiladi. Nozik, qo‘llanmaydigan yoki inson talab qilingan holat handoff yaratadi.
5. Provider payload, secret, hidden reasoning, prompt va chain-of-thought oddiy API yoki log orqali ko‘rsatilmaydi.

Batafsil: [AI Runtime arxitekturasi](backend/docs/architecture/ai-conversation-runtime.md) va [API shartnomasi](backend/docs/api/ai-runtime-api.md).

## Bron faqat commit qilingan sig‘imni tasdiqlaydi

Xodimlar, Public Booking, Inbox, AI va Voice bitta Booking domenidan foydalanadi. Availability filial, xodim va resurs jadvallari, IANA timezone va DST, tanaffus, faol uchrashuv, buffer, tugamagan hold hamda capacity’ni birga hisoblaydi. PostgreSQL advisory/row lock’lari transaction ichida availability’ni qayta tekshiradi, shu bois ikkita parallel so‘rov bir slotni ola olmaydi. Reminder, waitlist, ko‘chirish, bekor qilish va confirmation token’lari idempotent; database commit’dan oldin tasdiq yuborilmaydi. [Booking arxitekturasi](backend/docs/booking.md)ni ko‘ring.

## Mahsulot ekranlari

<table>
  <tr>
    <td width="50%"><strong>Yagona Inbox + boshqariladigan AI draft</strong><br><img src="docs/assets/readme/inbox-ai.webp" alt="Sintetik mijoz suhbati va AI draft tasdiqlash boshqaruvlari bo‘lgan Yagona Inbox" width="100%"></td>
    <td width="50%"><strong>Bronlash + tasdiqlangan uchrashuv</strong><br><img src="docs/assets/readme/booking-calendar.webp" alt="Tasdiqlangan sintetik uchrashuv ko‘rsatilgan Booking workspace" width="100%"></td>
  </tr>
  <tr>
    <td width="50%"><strong>AI Automation</strong><br><img src="docs/assets/readme/ai-automation.webp" alt="Limitlar va xavfsiz suggest mode ko‘rsatilgan AI Automation sozlamalari" width="100%"></td>
    <td width="50%"><strong>Billing + kompaniya Wallet’i</strong><br><img src="docs/assets/readme/billing-wallet.webp" alt="Sintetik balans va o‘zgarmas ledger yozuvlari ko‘rsatilgan kompaniya Wallet’i" width="100%"></td>
  </tr>
</table>

## Repository xaritasi

```text
backend/                 Django monoliti, workers, provider adapters, testlar va API docs
frontend/apps/landing/   Ommaviy RU/UZ/EN sayt va aniq /video route
frontend/apps/client/    Lokalizatsiyalangan workspace, widget va public booking
frontend/apps/admin/     Alohida autentifikatsiyali Internal Super Admin
frontend/packages/       Umumiy API client, UI, brand va build configuration
docs/                    Navigatsiya, local setup va README media
deploy/                  Production-shaped backend uchun Nginx namunalari
```

## Tez boshlash

Docker, native backend uchun Python 3.12, Node.js 24+, Corepack va pnpm 11.21 kerak. Fake provider’lar xavfsiz default: CI va local exploration uchun real OpenAI, Meta, Google, Telegram yoki Twilio credential’lari talab qilinmaydi.

Docker, migration, xavfsiz `bootstrap_platform`, deterministik `seed_full_demo`, Landing/Client/Admin ishga tushirish va barcha tekshiruvlarning joriy buyruqlari yagona [local setup qo‘llanmasida](docs/development/local-setup.md) saqlanadi. Shunda uch tildagi README buyruqlari bir-biridan ajralib ketmaydi. Local URL’lar: Landing `:3000`, Client `:3001`, Admin `:3002`, API `:8000`; parol faqat stdin yoki himoyalangan secret file orqali beriladi.

Repository’da Docker Compose va Nginx deployment scaffolding bor, ammo `.github/workflows` yo‘q; shu sababli README CI badge ko‘rsatmaydi va to‘liq CI/CD deployment tayyor deb da’vo qilmaydi.

## Live rejimga o‘tish

Provider’larni birma-bir, avval sintetik sandbox traffic va fail-closed health check bilan faollashtiring:

- [OpenAI Responses runtime](backend/docs/architecture/ai-conversation-runtime.md)
- [Meta Instagram App Review](backend/docs/integrations/instagram-app-review.md)
- [Telegram Managed Bots](backend/docs/integrations/telegram-managed-bots.md)
- [Google Gmail setup](backend/docs/integrations/google-gmail-setup.md)
- [Twilio SMS setup](backend/docs/integrations/twilio-sms-setup.md)
- [Twilio + OpenAI Voice setup](backend/docs/integrations/twilio-openai-voice-setup.md)

Billing hozir ataylab faqat fake va ko‘rib chiqiladigan manual adapterlarni beradi. Live payment provider tanlash va yozish — alohida kelajak bosqichi.

## Hujjatlar

[Hujjatlar xaritasi](docs/README.md)dan boshlang: [multi-tenancy](backend/docs/architecture/multitenancy.md), [CRM](backend/docs/architecture/crm-core.md), [AI Runtime](backend/docs/architecture/ai-conversation-runtime.md), [Booking](backend/docs/booking.md), [Billing & Wallet](backend/docs/architecture/billing-subscriptions.md), [Public Web Chat](backend/docs/architecture/public-web-chat.md), [Instagram](backend/docs/architecture/instagram-messaging.md), [Telegram](backend/docs/architecture/telegram-managed-bots.md), [Gmail](backend/docs/architecture/gmail-email-integration.md), [SMS](backend/docs/architecture/sms-messaging.md), [Voice](backend/docs/architecture/voice-ai-telephony.md), [Internal Super Admin](backend/docs/architecture/internal-control-plane.md) va [backend API map](backend/README.md).

## Xavfsizlik

Althair organization-scoped queryset, tekshirilgan destination routing, imzolangan provider webhook, write-only encrypted credential, idempotent mutation, MFA’li alohida internal authentication va secret scanning’dan foydalanadi. Platform staff customer session yoki superuser bypass olmaydi. Zaiflik haqida [GitHub Security Advisories](https://github.com/Rakhmatullo929/althair/security/advisories/new) orqali maxfiy xabar bering; nozik ma’lumot yuborishdan oldin [SECURITY.md](SECURITY.md)ni o‘qing.

---

<div align="center">

Birorta mijoz murojaatini yo‘qotishga haqqi yo‘q xizmat ko‘rsatuvchi bizneslar uchun yaratilgan.

[althair-ai.com](https://www.althair-ai.com/) · [Demoni ko‘rish](https://www.althair-ai.com/video)

</div>
