from django.contrib import admin

from ai_runtime.models import (
    AIDraft,
    AIHandoff,
    AIRun,
    AIToolCall,
    AIToolPolicy,
    AIUsageEvent,
    ConversationSummary,
    OrganizationAIRuntimeConfig,
)


for model in (
    OrganizationAIRuntimeConfig,
    AIToolPolicy,
    AIRun,
    AIToolCall,
    AIDraft,
    AIHandoff,
    AIUsageEvent,
    ConversationSummary,
):
    admin.site.register(model)
