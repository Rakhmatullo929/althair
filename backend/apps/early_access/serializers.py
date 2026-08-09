import re

from rest_framework import serializers


CONTACT_PATTERN = re.compile(r"^(?:[^\s@]+@[^\s@]+\.[^\s@]+|[+]?\d[\d\s().-]{6,20})$")


class EarlyAccessLeadSerializer(serializers.Serializer):
    fullName = serializers.CharField(min_length=2, max_length=100, trim_whitespace=True)
    companyName = serializers.CharField(min_length=2, max_length=120, trim_whitespace=True)
    contact = serializers.CharField(min_length=5, max_length=160, trim_whitespace=True)
    industry = serializers.CharField(min_length=1, max_length=80, trim_whitespace=True)
    preferredChannel = serializers.CharField(min_length=1, max_length=40, trim_whitespace=True)
    note = serializers.CharField(max_length=1000, allow_blank=True, required=False, default="", trim_whitespace=True)
    consent = serializers.BooleanField()
    website = serializers.CharField(max_length=200, allow_blank=True, required=False, default="")
    startedAt = serializers.IntegerField(min_value=1, required=False)
    receivedAt = serializers.DateTimeField(required=False)
    locale = serializers.ChoiceField(choices=["ru", "uz", "en"])
    source = serializers.CharField(max_length=80, allow_blank=True, required=False, default="landing")
    utm = serializers.DictField(required=False, default=dict)

    def validate_contact(self, value):
        value = value.strip()
        if not CONTACT_PATTERN.fullmatch(value):
            raise serializers.ValidationError("Enter a valid email address or phone number.")
        return value.lower() if "@" in value else re.sub(r"\s+", " ", value)

    def validate_consent(self, value):
        if value is not True:
            raise serializers.ValidationError("Consent is required.")
        return value

    def validate_utm(self, value):
        allowed = {"source", "medium", "campaign", "term", "content"}
        normalized = {}
        for key, item in value.items():
            if key in allowed and isinstance(item, str):
                normalized[key] = item.strip()[:160]
        return normalized
