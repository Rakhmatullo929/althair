#!/bin/sh
set -eu

E2E_DATABASE_PATH=/tmp/aifo-client-portal-e2e.sqlite3
rm -f "$E2E_DATABASE_PATH"

export DEBUG=True
export E2E_TESTING=True
export USE_SQLITE=1
export SQLITE_PATH="$E2E_DATABASE_PATH"
export CLIENT_APP_URL="http://localhost:3001"
FIELD_ENCRYPTION_KEY="$(../../../backend/.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export FIELD_ENCRYPTION_KEY
export CLIENT_PORTAL_SEED_PASSWORD="client-portal-development-only-password"

cd ../../../backend
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py seed_client_portal
exec .venv/bin/python manage.py runserver 127.0.0.1:8011 --noreload
