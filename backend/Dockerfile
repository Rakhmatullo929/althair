FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /application

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system appgroup \
    && adduser --system --ingroup appgroup --no-create-home appuser

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /application/entrypoint.sh \
    && mkdir -p /application/staticfiles /application/media \
    && chown -R appuser:appgroup /application

USER appuser
EXPOSE 8000
ENTRYPOINT ["/application/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120"]
