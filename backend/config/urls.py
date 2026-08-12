from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.http import JsonResponse
from organizations.views import MeView


def health_live(request):
    return JsonResponse({'status': 'ok'})


def health_ready(request):
    checks = {}
    healthy = True

    # PostgreSQL — always required
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        checks['postgres'] = 'ok'
    except Exception as e:
        checks['postgres'] = f'error: {type(e).__name__}'
        healthy = False

    try:
        from django.core.cache import cache
        cache.set('health:ready', 'ok', timeout=5)
        checks['redis'] = 'ok' if cache.get('health:ready') == 'ok' else 'error'
        healthy = healthy and checks['redis'] == 'ok'
    except Exception as e:
        checks['redis'] = f'error: {type(e).__name__}'
        healthy = False

    if settings.AI_RUNTIME_ENABLE_REAL_OPENAI and settings.AI_RUNTIME_PROVIDER == 'openai':
        checks['ai_runtime'] = 'configured' if settings.OPENAI_API_KEY else 'openai_key_missing'
        healthy = healthy and bool(settings.OPENAI_API_KEY)
    else:
        checks['ai_runtime'] = 'fake_or_disabled'

    checks['web_chat'] = 'disabled' if not settings.WEB_CHAT_ENABLE_PUBLIC else 'enabled'

    if settings.META_INSTAGRAM_ENABLE_LIVE:
        instagram_ready = all([
            settings.META_APP_ID,
            settings.META_APP_SECRET,
            settings.META_INSTAGRAM_VERIFY_TOKEN,
            settings.META_INSTAGRAM_GRAPH_API_VERSION,
            settings.META_INSTAGRAM_REDIRECT_URI,
        ])
        checks['instagram'] = 'configured' if instagram_ready else 'configuration_incomplete'
        healthy = healthy and instagram_ready
    else:
        checks['instagram'] = 'fake_or_disabled'

    if settings.TELEGRAM_ENABLE_LIVE:
        telegram_ready = all([
            settings.TELEGRAM_MANAGER_BOT_TOKEN,
            settings.TELEGRAM_MANAGER_BOT_USERNAME,
            settings.TELEGRAM_MANAGER_WEBHOOK_URL,
            settings.TELEGRAM_MANAGER_WEBHOOK_SECRET,
            settings.TELEGRAM_BOT_WEBHOOK_BASE_URL,
        ])
        checks['telegram'] = 'configured' if telegram_ready else 'configuration_incomplete'
        healthy = healthy and telegram_ready
    else:
        checks['telegram'] = 'fake_or_disabled'

    if settings.GOOGLE_GMAIL_ENABLE_LIVE:
        gmail_ready = all([
            settings.GOOGLE_GMAIL_CLIENT_ID,
            settings.GOOGLE_GMAIL_CLIENT_SECRET,
            settings.GOOGLE_GMAIL_REDIRECT_URI,
            settings.GOOGLE_GMAIL_PUBSUB_TOPIC,
            settings.GOOGLE_GMAIL_PUBSUB_AUDIENCE,
            settings.GOOGLE_GMAIL_PUBSUB_SERVICE_ACCOUNT,
            settings.GOOGLE_GMAIL_PUBSUB_SUBSCRIPTION,
        ])
        checks['gmail'] = 'configured' if gmail_ready else 'configuration_incomplete'
        healthy = healthy and gmail_ready
    else:
        checks['gmail'] = 'fake_or_disabled'

    if settings.SMS_ENABLE_LIVE:
        sms_ready = all([
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
            settings.SMS_PUBLIC_BASE_URL.startswith('https://'),
        ])
        checks['sms'] = 'configured' if sms_ready else 'configuration_incomplete'
        healthy = healthy and sms_ready
    else:
        checks['sms'] = 'fake_or_disabled'

    status_code = 200 if healthy else 503
    return JsonResponse({'status': 'ready' if healthy else 'degraded', 'checks': checks}, status=status_code)


urlpatterns = [
    path('grappelli/', include('grappelli.urls')),
    path('health/live', health_live, name='health_live'),
    path('health/ready', health_ready, name='health_ready'),
    path(settings.ADMIN_URL, admin.site.urls),
    path('core/', include(('core.urls', 'core'), namespace='core')),
    path('api/v1/', include([
        path('me/', MeView.as_view(), name='me'),
        path('organizations/', include(('organizations.urls', 'organizations'), namespace='organizations')),
        path('channel-connections/', include(('channels.urls', 'channels'), namespace='channels')),
        path('assistant-context/', include(('assistant_context.urls', 'assistant_context'), namespace='assistant_context')),
        path('', include(('crm.urls', 'crm'), namespace='crm')),
        path('', include(('ai_runtime.urls', 'ai_runtime'), namespace='ai_runtime')),
        path('', include(('web_chat.urls', 'web_chat'), namespace='web_chat')),
        path('', include(('instagram.urls', 'instagram'), namespace='instagram')),
        path('', include(('telegram.urls', 'telegram'), namespace='telegram')),
        path('', include(('gmail_integration.urls', 'gmail_integration'), namespace='gmail_integration')),
        path('', include(('sms.urls', 'sms'), namespace='sms')),
        path('webhooks/', include(('instagram.webhook_urls', 'instagram_webhooks'), namespace='instagram_webhooks')),
        path('webhooks/', include(('telegram.webhook_urls', 'telegram_webhooks'), namespace='telegram_webhooks')),
        path('webhooks/', include(('gmail_integration.webhook_urls', 'gmail_webhooks'), namespace='gmail_webhooks')),
        path('webhooks/', include(('sms.webhook_urls', 'sms_webhooks'), namespace='sms_webhooks')),
        path('public/web-chat/', include(('web_chat.public_urls', 'web_chat_public'), namespace='web_chat_public')),
        path('public/', include(('early_access.urls', 'early_access'), namespace='early_access')),
        path('users/', include(('users.urls', 'users'), namespace='users')),
        path('intake/', include(('intake.urls', 'intake'), namespace='intake')),
        path('jobs/', include(('jobs.urls', 'jobs'), namespace='jobs')),
    ])),
]

# Debug-only: Swagger, Redoc
if settings.DEBUG:
    from django.conf.urls.static import static
    from drf_yasg.views import get_schema_view
    from drf_yasg import openapi
    from rest_framework import permissions

    schema_view = get_schema_view(
        openapi.Info(title='API', default_version='v1'),
        public=True,
        permission_classes=(permissions.AllowAny,),
    )

    urlpatterns = [
        path('swagger<format>/', schema_view.without_ui(cache_timeout=0), name='schema-json'),
        path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
        path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    ] + urlpatterns
