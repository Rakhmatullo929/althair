# celery.py
import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
# Initialize Celery
app = Celery('config')

# app.config_from_object('celeryconfig')

app.config_from_object('django.conf:settings', namespace='CELERY')


# Discover celery_tasks in all Django apps
app.autodiscover_tasks()
