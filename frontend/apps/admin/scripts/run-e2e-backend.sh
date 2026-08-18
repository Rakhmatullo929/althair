#!/bin/sh
set -eu

E2E_DATABASE_PATH=/tmp/althair-admin-e2e.sqlite3
rm -f "$E2E_DATABASE_PATH"

export DEBUG=True
export E2E_TESTING=True
export USE_SQLITE=1
export SQLITE_PATH="$E2E_DATABASE_PATH"
export CLIENT_APP_URL="http://localhost:3001"
export CONTROL_PLANE_ENABLE=True
export CONTROL_PLANE_FAKE_MFA=True
export CONTROL_PLANE_MFA_REQUIRED=True
export CONTROL_PLANE_COOKIE_NAME="althair-internal-e2e"
export CONTROL_PLANE_SESSION_MINUTES=30
export CONTROL_PLANE_INACTIVITY_MINUTES=15
export CONTROL_PLANE_RECENT_MFA_MINUTES=10
export CONTROL_PLANE_SEED_PASSWORD="internal-platform-development-only"
export ENABLE_CRM_TEST_CHANNEL=True
export AI_RUNTIME_PROVIDER=fake
export AI_INTERNAL_TEST_AUTOPILOT=True
export AI_RUNTIME_ENABLE_REAL_OPENAI=False
export META_INSTAGRAM_ENABLE_LIVE=False
export META_INSTAGRAM_FAKE_PROVIDER=True
export TELEGRAM_ENABLE_LIVE=False
export TELEGRAM_FAKE_PROVIDER=True
export GOOGLE_GMAIL_ENABLE_LIVE=False
export GOOGLE_GMAIL_FAKE_PROVIDER=True
export SMS_ENABLE_LIVE=False
export SMS_FAKE_PROVIDER=True
export VOICE_ENABLE_LIVE=False
export VOICE_FAKE_PROVIDER=True
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
export FULL_DEMO_SEED_PASSWORD="internal-platform-development-only"

cd ../../../backend
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_full_demo --with-admin --with-wallet --non-interactive
exec .venv/bin/python manage.py runserver 127.0.0.1:8012 --noreload
