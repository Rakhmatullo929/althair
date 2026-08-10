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
FIELD_ENCRYPTION_KEY="$(../../../backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export FIELD_ENCRYPTION_KEY
export CLIENT_PORTAL_SEED_PASSWORD="client-portal-development-only-password"

cd ../../../backend
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_client_portal
.venv/bin/python manage.py seed_crm
.venv/bin/python manage.py seed_web_chat_demo
exec .venv/bin/python manage.py runserver 127.0.0.1:8011 --noreload
