from django.urls import path

from ai_runtime.views import (
    ConversationRunsView,
    DraftApproveView,
    DraftEditView,
    DraftRejectView,
    GenerateDraftView,
    HandoffAcknowledgeView,
    HandoffAssignView,
    HandoffResolveView,
    PauseAIView,
    ResumeAIView,
    RunDetailView,
    RunListView,
    RuntimeConfigView,
    ToolCallApproveView,
    ToolCallRejectView,
    ToolPoliciesView,
    UsageView,
)


urlpatterns = [
    path("ai/runtime-config/", RuntimeConfigView.as_view(), name="ai-runtime-config"),
    path("ai/tool-policies/", ToolPoliciesView.as_view(), name="ai-tool-policies"),
    path("ai/runs/", RunListView.as_view(), name="ai-runs"),
    path("ai/runs/<uuid:run_id>/", RunDetailView.as_view(), name="ai-run-detail"),
    path("ai/usage/", UsageView.as_view(), name="ai-usage"),
    path("conversations/<uuid:conversation_id>/ai/generate-draft/", GenerateDraftView.as_view(), name="ai-generate-draft"),
    path("conversations/<uuid:conversation_id>/ai/pause/", PauseAIView.as_view(), name="ai-pause"),
    path("conversations/<uuid:conversation_id>/ai/resume/", ResumeAIView.as_view(), name="ai-resume"),
    path("conversations/<uuid:conversation_id>/ai/runs/", ConversationRunsView.as_view(), name="conversation-ai-runs"),
    path("ai/drafts/<uuid:draft_id>/approve/", DraftApproveView.as_view(), name="ai-draft-approve"),
    path("ai/drafts/<uuid:draft_id>/edit-and-send/", DraftEditView.as_view(), name="ai-draft-edit"),
    path("ai/drafts/<uuid:draft_id>/reject/", DraftRejectView.as_view(), name="ai-draft-reject"),
    path("ai/tool-calls/<uuid:tool_call_id>/approve/", ToolCallApproveView.as_view(), name="ai-tool-approve"),
    path("ai/tool-calls/<uuid:tool_call_id>/reject/", ToolCallRejectView.as_view(), name="ai-tool-reject"),
    path("ai/handoffs/<uuid:handoff_id>/acknowledge/", HandoffAcknowledgeView.as_view(), name="ai-handoff-ack"),
    path("ai/handoffs/<uuid:handoff_id>/assign/", HandoffAssignView.as_view(), name="ai-handoff-assign"),
    path("ai/handoffs/<uuid:handoff_id>/resolve/", HandoffResolveView.as_view(), name="ai-handoff-resolve"),
]
