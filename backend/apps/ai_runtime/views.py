from __future__ import annotations

import uuid

from django.conf import settings
from django.db import transaction
from django.db.models import Avg, Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_runtime.models import AIDraft, AIHandoff, AIRun, AIToolCall, AIToolPolicy, AIUsageEvent
from ai_runtime.serializers import (
    DraftSerializer,
    HandoffSerializer,
    RunSerializer,
    RuntimeConfigSerializer,
    ToolCallSerializer,
    ToolPolicySerializer,
)
from ai_runtime.services import (
    AIRuntimeConflict,
    AIRuntimeLimit,
    AIRuntimeUnavailable,
    act_on_draft,
    approve_tool_call,
    ensure_runtime_config,
    queue_manual_run,
    reject_tool_call,
    set_conversation_ai_state,
    supersede_active_runs,
    update_handoff,
)
from ai_runtime.tasks import process_ai_run
from ai_runtime.tools import TOOL_REGISTRY
from core.api.pagination import StandardPagination
from crm.models import Conversation, ConversationAIState
from organizations.models import OrganizationMembership, OrganizationMembershipRole, OrganizationMembershipStatus
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin
from organizations.policies import role_allows


class AIBaseView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "operate"


class ManageAIView(AIBaseView):
    write_action = "manage_settings"


def _conflict(code):
    return Response({"detail": code, "code": code}, status=status.HTTP_409_CONFLICT)


def _limit(code):
    return Response({"detail": code, "code": code}, status=status.HTTP_429_TOO_MANY_REQUESTS)


def _unavailable(code):
    return Response({"detail": code, "code": code}, status=status.HTTP_409_CONFLICT)


def _paginate(request, view, rows, serializer):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(rows, request, view=view)
    return paginator.get_paginated_response(serializer(page, many=True).data)


class RuntimeConfigView(ManageAIView):
    def get(self, request):
        config = ensure_runtime_config(request.organization)
        config._real_openai_enabled = settings.AI_RUNTIME_ENABLE_REAL_OPENAI
        return Response(RuntimeConfigSerializer(config, context={"request": request}).data)

    def patch(self, request):
        config = ensure_runtime_config(request.organization)
        serializer = RuntimeConfigSerializer(config, data=request.data, partial=True, context={"request": request})
        serializer.is_valid(raise_exception=True)
        config = serializer.save(updated_by=request.organization_membership)
        config.full_clean()
        config.save()
        return Response(RuntimeConfigSerializer(config, context={"request": request}).data)


class ToolPoliciesView(ManageAIView):
    def get(self, request):
        ensure_runtime_config(request.organization)
        rows = AIToolPolicy.objects.for_organization(request.organization).order_by("tool_name")
        return Response(ToolPolicySerializer(rows, many=True).data)

    @transaction.atomic
    def patch(self, request):
        policies = request.data.get("policies")
        if not isinstance(policies, list) or not policies:
            return Response({"policies": ["Provide a non-empty policies list."]}, status=400)
        known = {item.tool_name: item for item in AIToolPolicy.objects.select_for_update().for_organization(request.organization)}
        seen = set()
        for item in policies:
            name = item.get("tool_name") if isinstance(item, dict) else None
            if name in seen or name not in known or name not in TOOL_REGISTRY:
                return Response({"policies": ["Unknown or duplicate tool policy."]}, status=400)
            seen.add(name)
            policy = known[name]
            spec = TOOL_REGISTRY[name]
            if spec.always_available and (item.get("enabled") is False or item.get("execution_mode") == "disabled"):
                return Response({"policies": ["Human handoff cannot be disabled."]}, status=400)
            enabled = bool(item.get("enabled", policy.enabled))
            mode = item.get("execution_mode", policy.execution_mode)
            if not enabled:
                mode = "disabled"
            if mode not in {"automatic", "require_approval", "disabled"}:
                return Response({"policies": ["Invalid execution mode."]}, status=400)
            policy.enabled = enabled
            policy.execution_mode = mode
            policy.configuration = item.get("configuration", policy.configuration)
            policy.updated_by = request.organization_membership
            policy.version += 1
            policy.full_clean()
            policy.save()
        rows = AIToolPolicy.objects.for_organization(request.organization).order_by("tool_name")
        return Response(ToolPolicySerializer(rows, many=True).data)


class RunListView(AIBaseView):
    def get(self, request):
        rows = AIRun.objects.for_organization(request.organization).select_related(
            "ai_context_revision", "conversation", "trigger_message"
        ).prefetch_related("tool_calls__approved_by__user", "handoffs", "draft")
        if value := request.query_params.get("conversation"):
            rows = rows.filter(conversation_id=value)
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return _paginate(request, self, rows, RunSerializer)


class RunDetailView(AIBaseView):
    def get(self, request, run_id):
        run = get_object_or_404(
            AIRun.objects.for_organization(request.organization)
            .select_related("ai_context_revision", "conversation", "trigger_message")
            .prefetch_related("tool_calls__approved_by__user", "handoffs", "draft"),
            pk=run_id,
        )
        return Response(RunSerializer(run).data)


class UsageView(AIBaseView):
    def get(self, request):
        today = timezone.localdate()
        month = today.replace(day=1)
        events = AIUsageEvent.objects.for_organization(request.organization)
        totals = events.filter(month_bucket=month).aggregate(
            input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"), cached_tokens=Sum("cached_tokens")
        )
        runs = AIRun.objects.for_organization(request.organization)
        counts = {item["status"]: item["count"] for item in runs.values("status").annotate(count=Count("id"))}
        outcomes = {item["outcome"]: item["count"] for item in runs.values("outcome").annotate(count=Count("id"))}
        drafts = AIDraft.objects.for_organization(request.organization)
        draft_counts = {
            item["status"]: item["count"]
            for item in drafts.values("status").annotate(count=Count("id"))
        }
        tools = AIToolCall.objects.for_organization(request.organization)
        tool_counts = {
            item["status"]: item["count"]
            for item in tools.values("status").annotate(count=Count("id"))
        }
        total_runs = runs.count()
        handoff_runs = outcomes.get("handoff", 0)
        return Response({
            "date": str(today),
            "month": str(month),
            "daily_runs": events.filter(date_bucket=today).count(),
            "monthly_input_tokens": totals["input_tokens"] or 0,
            "monthly_output_tokens": totals["output_tokens"] or 0,
            "monthly_cached_tokens": totals["cached_tokens"] or 0,
            "status_counts": counts,
            "outcome_counts": outcomes,
            "draft_status_counts": draft_counts,
            "tool_status_counts": tool_counts,
            "average_provider_latency_ms": round(
                runs.aggregate(value=Avg("latency_ms"))["value"] or 0
            ),
            "handoff_rate": handoff_runs / total_runs if total_runs else 0,
            "stale_run_cancellations": runs.filter(
                status="superseded", error_category="stale_context"
            ).count(),
        })


class ConversationAIActionView(AIBaseView):
    action = ""

    def post(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.for_organization(request.organization).select_related("contact", "channel_connection"),
            pk=conversation_id,
        )
        try:
            if self.action == "generate":
                request_key = request.headers.get("Idempotency-Key", "").strip() or str(uuid.uuid4())
                run, _ = queue_manual_run(
                    conversation=conversation, actor=request.organization_membership, request_key=request_key[:80]
                )
                process_ai_run.delay(str(run.id))
                run.refresh_from_db()
                return Response(RunSerializer(run).data, status=status.HTTP_202_ACCEPTED)
            if self.action == "pause":
                conversation.ai_state = ConversationAIState.PAUSED_BY_HUMAN
                conversation.ai_state_updated_at = timezone.now()
                conversation.save(update_fields=["ai_state", "ai_state_updated_at", "updated_at"])
                supersede_active_runs(conversation=conversation, reason="paused_by_human")
                return Response({"ai_state": conversation.ai_state})
            mode = request.data.get("mode", "suggest")
            if request.organization_membership.role == OrganizationMembershipRole.AGENT:
                raise PermissionDenied("Agents may pause AI but cannot resume it.")
            conversation = set_conversation_ai_state(
                conversation=conversation, actor=request.organization_membership, state=mode
            )
            return Response({"ai_state": conversation.ai_state})
        except AIRuntimeConflict as exc:
            return _conflict(str(exc))
        except AIRuntimeLimit as exc:
            return _limit(str(exc))
        except AIRuntimeUnavailable as exc:
            return _unavailable(str(exc))


class GenerateDraftView(ConversationAIActionView):
    action = "generate"


class PauseAIView(ConversationAIActionView):
    action = "pause"


class ResumeAIView(ConversationAIActionView):
    action = "resume"


class ConversationRunsView(RunListView):
    def get(self, request, conversation_id):
        get_object_or_404(Conversation.objects.for_organization(request.organization), pk=conversation_id)
        rows = AIRun.objects.for_organization(request.organization).filter(conversation_id=conversation_id).select_related(
            "ai_context_revision", "conversation", "trigger_message"
        ).prefetch_related("tool_calls__approved_by__user", "handoffs", "draft")
        return _paginate(request, self, rows, RunSerializer)


class DraftActionView(AIBaseView):
    action = "approve"

    def post(self, request, draft_id):
        draft = get_object_or_404(AIDraft.objects.for_organization(request.organization), pk=draft_id)
        try:
            draft = act_on_draft(
                draft=draft,
                actor=request.organization_membership,
                action=self.action,
                body=request.data.get("body"),
                rejection_reason=str(request.data.get("reason", "")),
            )
        except AIRuntimeConflict as exc:
            return _conflict(str(exc))
        return Response(DraftSerializer(draft).data)


class DraftApproveView(DraftActionView):
    action = "approve"


class DraftEditView(DraftActionView):
    action = "edit"


class DraftRejectView(DraftActionView):
    action = "reject"


class ToolCallActionView(AIBaseView):
    write_action = "manage_crm"
    action = "approve"

    def post(self, request, tool_call_id):
        call = get_object_or_404(AIToolCall.objects.for_organization(request.organization), pk=tool_call_id)
        try:
            call = (
                approve_tool_call(call=call, actor=request.organization_membership)
                if self.action == "approve"
                else reject_tool_call(call=call, actor=request.organization_membership)
            )
        except AIRuntimeConflict as exc:
            return _conflict(str(exc))
        return Response(ToolCallSerializer(call).data)


class ToolCallApproveView(ToolCallActionView):
    action = "approve"


class ToolCallRejectView(ToolCallActionView):
    action = "reject"


class HandoffActionView(AIBaseView):
    action = "acknowledge"

    def post(self, request, handoff_id):
        handoff = get_object_or_404(AIHandoff.objects.for_organization(request.organization), pk=handoff_id)
        if self.action in {"assign", "resolve"} and not role_allows(request.organization_membership.role, "manage_crm"):
            raise PermissionDenied("Your role cannot manage this handoff action.")
        assigned = None
        if self.action == "assign":
            assigned = get_object_or_404(
                OrganizationMembership.objects.filter(
                    organization=request.organization, status=OrganizationMembershipStatus.ACTIVE
                ),
                pk=request.data.get("membership_id"),
            )
        try:
            handoff = update_handoff(
                handoff=handoff,
                actor=request.organization_membership,
                action=self.action,
                assigned_membership=assigned,
            )
        except AIRuntimeConflict as exc:
            return _conflict(str(exc))
        return Response(HandoffSerializer(handoff).data)


class HandoffAcknowledgeView(HandoffActionView):
    action = "acknowledge"


class HandoffAssignView(HandoffActionView):
    action = "assign"


class HandoffResolveView(HandoffActionView):
    action = "resolve"
