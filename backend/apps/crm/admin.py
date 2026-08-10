from django.contrib import admin

from crm.models import (
    Contact,
    ContactIdentity,
    ContactNote,
    Conversation,
    CrmActivity,
    FollowUpTask,
    Lead,
    Message,
    Pipeline,
    PipelineStage,
    Tag,
)


for model in (
    Contact,
    ContactIdentity,
    ContactNote,
    Tag,
    Conversation,
    Message,
    Pipeline,
    PipelineStage,
    Lead,
    FollowUpTask,
    CrmActivity,
):
    admin.site.register(model)
