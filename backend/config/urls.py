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
