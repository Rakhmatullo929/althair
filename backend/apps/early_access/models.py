import uuid

from django.db import models


class EarlyAccessLead(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField(max_length=100)
    company_name = models.CharField(max_length=120)
    contact = models.CharField(max_length=160, db_index=True)
    industry = models.CharField(max_length=80)
    preferred_channel = models.CharField(max_length=40)
    note = models.TextField(max_length=1000, blank=True)
    consent = models.BooleanField()
    locale = models.CharField(max_length=2, choices=[("ru", "RU"), ("uz", "UZ"), ("en", "EN")])
    source = models.CharField(max_length=80, default="landing", blank=True)
    utm = models.JSONField(default=dict, blank=True)
    payload_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["locale", "created_at"])]

    def __str__(self) -> str:
        return f"{self.company_name} ({self.locale})"
