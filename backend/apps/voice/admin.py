from django.contrib import admin

from voice.models import (
    VoiceAuditEvent,
    VoiceCall,
    VoiceCarrierStatusEvent,
    VoiceConnection,
    VoiceControllerJob,
    VoiceToolCall,
    VoiceTranscriptSegment,
    VoiceTransferAttempt,
    VoiceTransferDestination,
    VoiceUsageEvent,
    VoiceWebhookEnvelope,
)


for model in (
    VoiceConnection,
    VoiceTransferDestination,
    VoiceCall,
    VoiceTranscriptSegment,
    VoiceWebhookEnvelope,
    VoiceControllerJob,
    VoiceToolCall,
    VoiceTransferAttempt,
    VoiceUsageEvent,
    VoiceCarrierStatusEvent,
    VoiceAuditEvent,
):
    admin.site.register(model)
