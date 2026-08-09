# AI Front Office backend

Django 5.2 modular monolith for organization, channel, and tenant-safe legacy intake/job workflows.

## Local Python setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
# Replace placeholders and generate FIELD_ENCRYPTION_KEY as described in .env.example.
python manage.py migrate --noinput
python manage.py createsuperuser
python manage.py seed_dev_workspace
python manage.py runserver 0.0.0.0:8000
```

PostgreSQL and Redis are the supported runtime services. SQLite via `USE_SQLITE=1` exists for fast
local/test verification only.

## Checks

```bash
python manage.py makemigrations --check
python manage.py check
python manage.py test
python -m compileall -q .
```

See `docs/architecture/multitenancy.md`, `docs/api/multitenant-api.md`, and
`docs/security/secret-rotation-required.md` before deployment.
