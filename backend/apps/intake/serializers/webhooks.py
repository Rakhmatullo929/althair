from rest_framework import serializers


class TwilioVoiceWebhookSerializer(serializers.Serializer):
    """
    Accepts Twilio Voice status-callback payloads AND transcription-callback payloads
    through a single endpoint.  All fields are optional — Twilio omits fields that are
    not yet available (e.g. TranscriptionText is absent on the initial status callback).

    Also accepts extended structured fields forwarded by a voice-AI / IVR platform
    (builder, service_type, etc.) so that richer job records can be created directly
    from the webhook payload without a separate API call.
    """

    # ── Core Twilio Voice fields ──────────────────────────────────────────────
    CallSid = serializers.CharField(required=False, allow_blank=True, default='')
    AccountSid = serializers.CharField(required=False, allow_blank=True, default='')
    From = serializers.CharField(required=False, allow_blank=True, default='')
    To = serializers.CharField(required=False, allow_blank=True, default='')
    CallStatus = serializers.CharField(required=False, allow_blank=True, default='')
    Direction = serializers.CharField(required=False, allow_blank=True, default='')

    # ── Recording fields (recording-status callback) ──────────────────────────
    RecordingSid = serializers.CharField(required=False, allow_blank=True, default='')
    RecordingUrl = serializers.CharField(required=False, allow_blank=True, default='')
    RecordingDuration = serializers.CharField(required=False, allow_blank=True, default='')

    # ── Transcription fields (transcription-status callback) ──────────────────
    TranscriptionSid = serializers.CharField(required=False, allow_blank=True, default='')
    TranscriptionText = serializers.CharField(required=False, allow_blank=True, default='')
    TranscriptionStatus = serializers.CharField(required=False, allow_blank=True, default='')

    # ── Optional AI/system summary (forwarded by voice-AI platform) ───────────
    ai_summary = serializers.CharField(required=False, allow_blank=True, default='')

    # ── Escalation marker — when the platform flags a call as a human handoff,
    #    we route it to the Escalation queue and do NOT create a job. ──────────
    is_escalation = serializers.BooleanField(required=False, default=False)
    category = serializers.CharField(required=False, allow_blank=True, default='', max_length=40)

    # ── Optional structured job fields (forwarded by voice-AI / IVR) ─────────
    builder = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    project = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    site = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    community = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    service_type = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    install_type = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    scope = serializers.CharField(required=False, allow_blank=True, default='')
    quantity = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    urgency = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    division = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    po_rec_note = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class VoiceEscalationSerializer(serializers.Serializer):
    """Payload the external Voice platform POSTs to escalate a call to a human.

    Mirrors the SMS escalation data (name if given, transcript, summary, reason)
    so voice escalations land in the same Escalation queue.  All fields optional.
    """
    phone = serializers.CharField(required=False, allow_blank=True, default='', max_length=30)
    destination = serializers.CharField(required=True, max_length=255)
    name = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    reason = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    transcript = serializers.CharField(required=False, allow_blank=True, default='')
    summary = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    call_sid = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    related_job_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class TwilioSmsWebhookSerializer(serializers.Serializer):
    MessageSid = serializers.CharField(required=False, allow_blank=True, default='')
    From = serializers.CharField(required=False, allow_blank=True, default='')
    To = serializers.CharField(required=False, allow_blank=True, default='')
    Body = serializers.CharField(required=False, allow_blank=True, default='')
    NumMedia = serializers.CharField(required=False, allow_blank=True, default='0')
    AccountSid = serializers.CharField(required=False, allow_blank=True, default='')
    SmsSid = serializers.CharField(required=False, allow_blank=True, default='')
    SmsStatus = serializers.CharField(required=False, allow_blank=True, default='')


class OutlookEmailWebhookSerializer(serializers.Serializer):
    to_email = serializers.EmailField(required=True)
    from_email = serializers.EmailField(required=False, allow_blank=True, default='')
    from_name = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    subject = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    body = serializers.CharField(required=False, allow_blank=True, default='')
    message_id = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    received_at = serializers.CharField(required=False, allow_blank=True, default='', max_length=50)

    builder = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    project = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    site = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    community = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    service_type = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)
    scope = serializers.CharField(required=False, allow_blank=True, default='')
    quantity = serializers.CharField(required=False, allow_blank=True, default='', max_length=100)
    urgency = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
