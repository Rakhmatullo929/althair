#!/bin/sh
set -eu

E2E_DATABASE_PATH=/tmp/aifo-client-portal-e2e.sqlite3
rm -f "$E2E_DATABASE_PATH"

export DEBUG=True
export E2E_TESTING=True
export USE_SQLITE=1
export SQLITE_PATH="$E2E_DATABASE_PATH"
export CLIENT_APP_URL="http://localhost:3001"
export ENABLE_CRM_TEST_CHANNEL=True
export AI_RUNTIME_PROVIDER=fake
export AI_INTERNAL_TEST_AUTOPILOT=True
export AI_RUNTIME_ENABLE_REAL_OPENAI=False
export AI_RUNTIME_GLOBAL_KILL_SWITCH=False
export WEB_CHAT_ENABLE_PUBLIC=True
export WEB_CHAT_GLOBAL_KILL_SWITCH=False
export WEB_CHAT_ALLOW_FAKE_AUTOPILOT=True
export WEB_CHAT_SESSION_SIGNING_KEY="e2e-only-web-chat-signing-key"
export WEB_CHAT_WIDGET_ORIGINS="http://localhost:3001"
export WEB_CHAT_DEMO_INSTALLATION_KEY="wc_demo_portal_test"
export META_APP_ID="fake-meta-app"
export META_APP_SECRET="test-only-meta-app-secret"
export META_INSTAGRAM_VERIFY_TOKEN="e2e-only-meta-verify-token"
export META_INSTAGRAM_GRAPH_API_VERSION="v-test"
export META_INSTAGRAM_REDIRECT_URI="http://127.0.0.1:8011/api/v1/integrations/instagram/oauth/callback/"
export META_INSTAGRAM_ENABLE_LIVE=False
export META_INSTAGRAM_ENABLE_HUMAN_AGENT=True
export META_INSTAGRAM_FAKE_PROVIDER=True
export TELEGRAM_ENABLE_LIVE=False
export TELEGRAM_FAKE_PROVIDER=True
export TELEGRAM_MANAGER_BOT_USERNAME="AlthairManagerBot"
export TELEGRAM_MANAGER_WEBHOOK_SECRET="test-only-telegram-manager-secret"
export TELEGRAM_BOT_WEBHOOK_BASE_URL="http://127.0.0.1:8011/api/v1/webhooks/telegram/bots"
export GOOGLE_GMAIL_ENABLE_LIVE=False
export GOOGLE_GMAIL_FAKE_PROVIDER=True
export GOOGLE_GMAIL_REDIRECT_URI="http://127.0.0.1:8011/api/v1/integrations/gmail/oauth/callback/"
export GOOGLE_GMAIL_PUBSUB_TOPIC="projects/e2e/topics/gmail-notifications"
export GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION="projects/e2e/subscriptions/gmail-push"
export GOOGLE_GMAIL_PUBSUB_AUDIENCE="http://127.0.0.1:8011/api/v1/webhooks/google/gmail-pubsub/"
export GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT="gmail-push@e2e.iam.gserviceaccount.com"
export GOOGLE_GMAIL_FAKE_PUBSUB_TOKEN="test-only-google-pubsub-oidc"
export SMS_ENABLE_LIVE=False
export SMS_FAKE_PROVIDER=True
export SMS_PUBLIC_BASE_URL="https://api.e2e.example.test"
export TWILIO_AUTH_TOKEN="test-only-twilio-webhook-token"
export VOICE_ENABLE_LIVE=False
export VOICE_CARRIER_PROVIDER=fake
export VOICE_REALTIME_PROVIDER=fake
export VOICE_FAKE_PROVIDER=True
export VOICE_GLOBAL_KILL_SWITCH=False
export VOICE_FAKE_WEBHOOK_SECRET="test-only-voice-webhook-secret"
export TWILIO_VOICE_PUBLIC_BASE_URL="https://api.e2e.example.test"
export TWILIO_VOICE_AUTH_TOKEN="test-only-twilio-voice-token"
export OPENAI_REALTIME_MODEL="configured-realtime-model"
export OPENAI_REALTIME_VOICE=marin
export BILLING_ENABLE=True
export BILLING_PROVIDER=fake
export BILLING_DEFAULT_PLAN_KEY=starter
export BILLING_DEFAULT_CURRENCY=UZS
export BILLING_TRIAL_DAYS=14
export BILLING_GRACE_DAYS=7
export BILLING_INVOICE_PREFIX=E2E
export BILLING_FAKE_PROVIDER=True
export BILLING_MANUAL_PROVIDER_ENABLE=True
export BOOKING_ENABLE=True
export BOOKING_PUBLIC_PAGE_ENABLE=True
export BOOKING_REMINDERS_ENABLE=True
export BOOKING_FAKE_NOTIFICATIONS=True
export BOOKING_REMINDER_PROVIDER=fake
FIELD_ENCRYPTION_KEY="$(../../../backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export FIELD_ENCRYPTION_KEY
export CLIENT_PORTAL_SEED_PASSWORD="client-portal-development-only-password"
export FULL_DEMO_SEED_PASSWORD="client-portal-development-only-password"

cd ../../../backend
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_full_demo --with-admin --with-wallet --non-interactive
exec .venv/bin/python manage.py runserver 127.0.0.1:8011 --noreload
