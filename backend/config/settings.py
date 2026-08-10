"""
Django settings for config project.
Production-hardened configuration.
Local dev overrides via settings_dev.py (imported at bottom).
Production secrets via Vault + Kubernetes.
"""

import os
import ssl
import sys
from datetime import timedelta
from corsheaders.defaults import default_headers

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Core Security
# ---------------------------------------------------------------------------
DEBUG = os.environ.get('DEBUG', 'False').lower() in ('true', '1', 'yes')
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY and not DEBUG and 'runserver' not in sys.argv and 'test' not in sys.argv:
    raise RuntimeError("SECRET_KEY environment variable is required in production")
SECRET_KEY = SECRET_KEY or 'django-insecure-dev-only-key-change-me'

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        'ALLOWED_HOSTS',
        'localhost,127.0.0.1',
    ).split(',')
    if h.strip()
]

E2E_TESTING = os.environ.get('E2E_TESTING', '').lower() in ('true', '1', 'yes')
TESTING = 'test' in sys.argv or E2E_TESTING

# Append module dir
sys.path.append(os.path.join(BASE_DIR, 'apps'))

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    'grappelli',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',

    # JWT
    'dj_rest_auth',
    'dj_rest_auth.registration',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',

    'storages',

    # APPS
    'core',
    'users',
    'intake',
    'jobs',
    'organizations',
    'channels',
    'early_access',
    'assistant_context',
    'crm',
    'ai_runtime',
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.request_id.RequestIDMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'core.middleware.security.SecurityHeadersMiddleware',
]

# ---------------------------------------------------------------------------
# CORS & CSRF
# ---------------------------------------------------------------------------
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ['DELETE', 'GET', 'OPTIONS', 'PATCH', 'POST', 'PUT']
CORS_ALLOW_HEADERS = (
    *default_headers,
    'idempotency-key',
    'x-organization-id',
    'x-request-id',
)

if DEBUG:
    CORS_ALLOWED_ORIGINS = [
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://localhost:3001',
        'http://127.0.0.1:3001',
        'http://localhost:5173',
        'http://127.0.0.1:5173',
    ]
    CSRF_TRUSTED_ORIGINS = [
        'http://localhost:3000',
        'http://127.0.0.1:3000',
        'http://localhost:3001',
        'http://127.0.0.1:3001',
        'http://localhost:5173',
        'http://127.0.0.1:5173',
    ]
else:
    CORS_ALLOWED_ORIGINS = [
        o.strip() for o in os.environ.get('CORS_ALLOWED_ORIGINS', '').split(',') if o.strip()
    ]
    CSRF_TRUSTED_ORIGINS = [
        o.strip()
        for o in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',')
        if o.strip()
    ]

# ---------------------------------------------------------------------------
# URL & Templates
# ---------------------------------------------------------------------------
ROOT_URLCONF = 'config.urls'
ASGI_APPLICATION = 'config.asgi.application'
WSGI_APPLICATION = 'config.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
if os.environ.get('USE_SQLITE', '').lower() in ('true', '1', 'yes'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': os.environ.get('SQLITE_PATH', os.path.join(BASE_DIR, 'db.sqlite3')),
        },
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('POSTGRES_NAME', 'aifo'),
            'USER': os.environ.get('POSTGRES_USER', 'aifo'),
            'PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
            'HOST': os.environ.get('POSTGRES_HOST', 'localhost'),
            'PORT': os.environ.get('POSTGRES_PORT', 5432),
            'CONN_MAX_AGE': 60,
            'OPTIONS': {
                'connect_timeout': 10,
            },
        },
    }

# ---------------------------------------------------------------------------
# Password Validation (hardened)
# ---------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------------------------------------------------------
# Celery (beat schedule via settings, no django-celery-beat)
# ---------------------------------------------------------------------------
CELERY_APP = 'config'
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_ENABLE_UTC = True
CELERY_IGNORE_RESULT = False
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_TASK_ALWAYS_EAGER = TESTING
CELERY_TASK_EAGER_PROPAGATES = TESTING

# Celery Beat schedule (add periodic tasks here)
# from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    # 'example_task': {
    #     'task': 'core.tasks.example_task',
    #     'schedule': crontab(minute='*/10'),
    # },
}

CACHES = {
    'default': {
        'BACKEND': (
            'django.core.cache.backends.locmem.LocMemCache'
            if TESTING or os.environ.get('USE_LOCMEM_CACHE', '').lower() in ('true', '1', 'yes')
            else 'django.core.cache.backends.redis.RedisCache'
        ),
        'LOCATION': 'aifo-tests' if TESTING else os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    },
}

# Redis over TLS: rediss:// requires explicit ssl_cert_reqs (Celery redis backend)
if CELERY_BROKER_URL.startswith("rediss://"):
    CELERY_BROKER_USE_SSL = {"ssl_cert_reqs": ssl.CERT_NONE}
if CELERY_RESULT_BACKEND.startswith("rediss://"):
    CELERY_REDIS_BACKEND_USE_SSL = {"ssl_cert_reqs": ssl.CERT_NONE}

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# AWS S3 Storage
# ---------------------------------------------------------------------------
AWS_S3_ACCESS_KEY_ID = os.getenv('AWS_S3_ACCESS_KEY_ID')
AWS_S3_SECRET_ACCESS_KEY = os.getenv('AWS_S3_SECRET_ACCESS_KEY')
AWS_S3_STORAGE_BUCKET_NAME = os.getenv('AWS_S3_STORAGE_BUCKET_NAME')
AWS_S3_REGION_NAME = os.getenv('AWS_S3_REGION_NAME', 'us-east-1')
AWS_S3_ENDPOINT_URL = os.getenv('AWS_S3_ENDPOINT_URL', None)
AWS_S3_SIGNATURE_VERSION = 's3v4'
AWS_DEFAULT_ACL = None
AWS_QUERYSTRING_AUTH = True
AWS_QUERYSTRING_EXPIRE = 3600

STORAGES = {
    'default': ({
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
        'OPTIONS': {'location': os.path.join(BASE_DIR, 'media')},
    } if DEBUG or not AWS_S3_STORAGE_BUCKET_NAME else {
        'BACKEND': 'storages.backends.s3.S3Storage',
        'OPTIONS': {
            'access_key': AWS_S3_ACCESS_KEY_ID,
            'secret_key': AWS_S3_SECRET_ACCESS_KEY,
            'bucket_name': AWS_S3_STORAGE_BUCKET_NAME,
            'region_name': AWS_S3_REGION_NAME,
            'endpoint_url': AWS_S3_ENDPOINT_URL,
            'location': 'media',
            'object_parameters': {'CacheControl': 'max-age=86400'},
            'default_acl': None,
            'querystring_auth': True,
            'querystring_expire': 3600,
            'file_overwrite': False,
            'signature_version': 's3v4',
        },
    }),
    'staticfiles': {
        'BACKEND': 'core.storage.NonStrictManifestStorage',
    },
}

# ---------------------------------------------------------------------------
# Static Files
# ---------------------------------------------------------------------------
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = 'users.User'

# ---------------------------------------------------------------------------
# JWT Configuration (hardened)
# ---------------------------------------------------------------------------
REST_USE_JWT = True
REST_AUTH_TOKEN_MODEL = None
REST_AUTH = {
    'TOKEN_MODEL': None,
}
JWT_AUTH_REFRESH_COOKIE = 'refresh'
JWT_AUTH_COOKIE = 'jwt-auth'

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_TOKEN_CLASSES': ('rest_framework_simplejwt.tokens.AccessToken',),
    'JTI_CLAIM': 'jti',
    'TOKEN_TYPE_CLAIM': 'token_type',
    'AUTH_COOKIE_PATH': '/',
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_COOKIE': 'jwt-auth',
    'AUTH_COOKIE_REFRESH': 'refresh',
    'AUTH_COOKIE_SECURE': not DEBUG,
    'AUTH_COOKIE_HTTP_ONLY': True,
    'AUTH_COOKIE_SAMESITE': 'Lax',
}

# ---------------------------------------------------------------------------
# REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'users.utils.authentication.CookieJWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.ScopedRateThrottle',
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'users': '30/min',
        'login': '5/min',
        'registration': '5/hour',
        'invitation': '10/min',
        'password_sensitive': '5/hour',
        'anon': '100/hour',
        'intake_webhook': '120/min',  # public webhook endpoints (Twilio / Outlook relay)
        'jobs': '120/min',  # Phase 2 operational job API (authenticated)
        'early_access': '30/hour',
        'crm_search': '120/min',
        'crm_message': '60/min',
    },
    'EXCEPTION_HANDLER': 'core.api.exception_handler.api_exception_handler',
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ) if not DEBUG else (
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ),
}

if os.environ.get('E2E_TESTING', '').lower() in ('true', '1', 'yes'):
    REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'].update({
        'login': '200/min',
        'registration': '200/min',
        'invitation': '200/min',
        'password_sensitive': '200/min',
        'anon': '1000/min',
        'crm_search': '1000/min',
        'crm_message': '1000/min',
    })

# ---------------------------------------------------------------------------
# Session & Cookie Security
# ---------------------------------------------------------------------------
# NOTE: SESSION_COOKIE_SECURE / CSRF_COOKIE_SECURE depend on DEBUG and are
# set at the bottom of this file (after settings_dev overrides are applied)
# so that local dev over HTTP can store session cookies.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 3600  # 1 hour
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# SSL, HSTS, and *_COOKIE_SECURE — set at the bottom so DEBUG reflects
# the final value after settings_dev overrides.

# ---------------------------------------------------------------------------
# Security Headers
# ---------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# ---------------------------------------------------------------------------
# File Upload Limits
# ---------------------------------------------------------------------------
DATA_UPLOAD_MAX_MEMORY_SIZE = 10485760  # 10 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 10485760  # 10 MB

# ---------------------------------------------------------------------------
# Logging (with security audit)
# ---------------------------------------------------------------------------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} {levelname} {name} {message}',
            'style': '{',
        },
        'security': {
            'format': '{asctime} SECURITY {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'level': 'ERROR',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'security_console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'security',
        },
    },
    'loggers': {
        '': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.security': {
            'handlers': ['security_console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'security.audit': {
            'handlers': ['security_console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

# ---------------------------------------------------------------------------
# Field Encryption
# ---------------------------------------------------------------------------
FIELD_ENCRYPTION_KEY = os.environ.get('FIELD_ENCRYPTION_KEY', '')
if not FIELD_ENCRYPTION_KEY and not DEBUG and 'test' not in sys.argv:
    raise RuntimeError(
        'FIELD_ENCRYPTION_KEY is not set. '
        'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" '
        'and add it to the production environment. '
        'All EncryptedTextField writes will fail without this key.'
    )

# ---------------------------------------------------------------------------
# External Services
# ---------------------------------------------------------------------------
FRONTEND_DOMAIN = os.environ.get('FRONTEND_DOMAIN', '')
BACKEND_DOMAIN = os.environ.get('BACKEND_DOMAIN', '')
CLIENT_APP_URL = os.environ.get('CLIENT_APP_URL', 'http://localhost:3001')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@example.test')
EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend',
)

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
ENABLE_CRM_TEST_CHANNEL = os.environ.get('ENABLE_CRM_TEST_CHANNEL', '').lower() in ('true', '1', 'yes')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', '')
OPENAI_REQUEST_TIMEOUT_SECONDS = int(os.environ.get('OPENAI_REQUEST_TIMEOUT_SECONDS', '30'))
OPENAI_MAX_RETRIES = int(os.environ.get('OPENAI_MAX_RETRIES', '2'))
AI_RUNTIME_PROVIDER = os.environ.get('AI_RUNTIME_PROVIDER', 'fake').strip().lower()
AI_RUNTIME_ENABLE_REAL_OPENAI = os.environ.get('AI_RUNTIME_ENABLE_REAL_OPENAI', '').lower() in ('true', '1', 'yes')
AI_INTERNAL_TEST_AUTOPILOT = os.environ.get('AI_INTERNAL_TEST_AUTOPILOT', '').lower() in ('true', '1', 'yes')
AI_MANUAL_GENERATION_PER_MINUTE = int(os.environ.get('AI_MANUAL_GENERATION_PER_MINUTE', '5'))
AI_MAX_TOOL_CALLS_PER_RUN = int(os.environ.get('AI_MAX_TOOL_CALLS_PER_RUN', '8'))

TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID', '')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
TWILIO_PHONE_NUMBER = os.environ.get('TWILIO_PHONE_NUMBER', '')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY', '')
SENDGRID_FROM_EMAIL = os.environ.get('SENDGRID_FROM_EMAIL', 'noreply@example.com')
SENDGRID_FROM_NAME = os.environ.get('SENDGRID_FROM_NAME', 'App')

# SMS
DEBUG_SMS = os.environ.get('DEBUG_SMS', 'False').lower() in ('true', '1', 'yes')

# Intake — shared secret for Outlook / Power Automate webhook verification
OUTLOOK_WEBHOOK_SECRET = os.environ.get('OUTLOOK_WEBHOOK_SECRET', '')
EARLY_ACCESS_WEBHOOK_SECRET = os.environ.get('EARLY_ACCESS_WEBHOOK_SECRET', '')
EARLY_ACCESS_RATE_LIMIT = int(os.environ.get('EARLY_ACCESS_RATE_LIMIT', '10'))

# Static API token (SHA256 hash) — used by StaticBearerAuthentication for
# server-to-server webhooks (e.g. Twilio voice/SMS callbacks).
EXPECTED_API_TOKEN_SHA256 = os.environ.get('EXPECTED_API_TOKEN_SHA256', '')

# Admin URL (customizable for security — hide from scanners)
ADMIN_URL = os.environ.get('ADMIN_URL', 'admin/')

# ---------------------------------------------------------------------------
# Sentry (production)
# ---------------------------------------------------------------------------
SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
if SENTRY_DSN and not DEBUG:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=False,
    )

# ---------------------------------------------------------------------------
# Local overrides (settings_dev.py)
# ---------------------------------------------------------------------------
try:
    from .settings_dev import *  # noqa: F401, F403
except ImportError:
    pass

# ---------------------------------------------------------------------------
# SSL, HSTS & secure-cookie flags — set AFTER settings_dev so DEBUG reflects
# the final value.  Otherwise dev over HTTP would still get 301-to-HTTPS,
# HSTS headers, and Secure-only cookies (which the browser refuses to send
# back, breaking admin login).
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() in ('true', '1', 'yes')
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'True').lower() in ('true', '1', 'yes')
    SECURE_HSTS_PRELOAD = os.environ.get('SECURE_HSTS_PRELOAD', 'True').lower() in ('true', '1', 'yes')
    if os.environ.get('TRUST_PROXY_HEADERS', 'True').lower() in ('true', '1', 'yes'):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    # Explicit OFF in dev — defends against any earlier import that may have
    # already enabled them, and lets admin login work over plain HTTP.
    SECURE_SSL_REDIRECT = False
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
