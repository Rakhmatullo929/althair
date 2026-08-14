from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from ai_runtime.context import PublishedContextRequired, build_runtime_context
from ai_runtime.models import (
    AIDraft,
    AIDraftStatus,
    AIHandoff,
    AIHandoffStatus,
    AIRun,
    AIRunOutcome,
    AIRunStatus,
    AIToolCall,
    AIToolCallStatus,
    AIToolPolicy,
    AIUsageEvent,
    HandoffRequestedBy,
    OrganizationAIRuntimeConfig,
    RuntimeMode,
    ToolExecutionMode,
)
from ai_runtime.prompts import build_prompt, select_language, validate_generated_text
from ai_runtime.providers import AIProviderError, provider_for
from ai_runtime.tools import (
    TOOL_REGISTRY,
    ToolPermissionError,
    ToolValidationError,
    execute_tool,
    provider_tools_for,
    validate_arguments,
)
from crm.models import (
    Conversation,
    ConversationAIState,
    Message,
    MessageContentType,
    MessageDirection,
    MessageSenderType,
    MessageStatus,
)
from crm.services import add_system_message, is_internal_test_connection, record_activity
from organizations.models import OrganizationMembership, OrganizationMembershipRole, OrganizationStatus
from control_plane.policies import operation_allowed


logger = logging.getLogger("ai_runtime")


class AIRuntimeConflict(Exception):
    pass


class AIRuntimeLimit(Exception):
    pass


class AIRuntimeUnavailable(Exception):
    pass


def ensure_runtime_config(organization):
    config, _ = OrganizationAIRuntimeConfig.objects.get_or_create(
        organization=organization,
        defaults={
            "provider": settings.AI_RUNTIME_PROVIDER,
            "model": settings.OPENAI_MODEL or "configured-model",
        },
    )
    existing = set(
        AIToolPolicy.objects.for_organization(organization).values_list("tool_name", flat=True)
    )
    AIToolPolicy.objects.bulk_create(
        [
            AIToolPolicy(
                organization=organization,
                tool_name=name,
                enabled=spec.always_available,
                execution_mode=(ToolExecutionMode.AUTOMATIC if spec.always_available else ToolExecutionMode.DISABLED),
            )
            for name, spec in TOOL_REGISTRY.items()
            if name not in existing
        ],
        ignore_conflicts=True,
    )
    return config


def default_ai_state_for_connection(organization, connection):
    try:
        from sms.services import ai_state_for_connection as sms_ai_state

        state = sms_ai_state(organization, connection)
        if state is not None:
            if state == ConversationAIState.OFF:
                return state
            config = ensure_runtime_config(organization)
            if not config.enabled:
                return ConversationAIState.OFF
            if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(pk=connection.pk).exists():
                return ConversationAIState.OFF
            return state
    except ImportError:
        pass
    try:
        from gmail_integration.services import ai_state_for_connection as gmail_ai_state

        state = gmail_ai_state(organization, connection)
        if state is not None:
            if state == ConversationAIState.OFF:
                return state
            config = ensure_runtime_config(organization)
            if not config.enabled:
                return ConversationAIState.OFF
            if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(pk=connection.pk).exists():
                return ConversationAIState.OFF
            return state
    except ImportError:
        pass
    try:
        from telegram.services import ai_state_for_connection as telegram_ai_state

        state = telegram_ai_state(organization, connection)
        if state is not None:
            if state == ConversationAIState.OFF:
                return state
            config = ensure_runtime_config(organization)
            if not config.enabled:
                return ConversationAIState.OFF
            if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(pk=connection.pk).exists():
                return ConversationAIState.OFF
            return state
    except ImportError:
        pass
    try:
        from instagram.services import ai_state_for_connection

        instagram_state = ai_state_for_connection(organization, connection)
        if instagram_state is not None:
            if instagram_state == ConversationAIState.OFF:
                return instagram_state
            config = ensure_runtime_config(organization)
            if not config.enabled:
                return ConversationAIState.OFF
            if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(
                pk=connection.pk
            ).exists():
                return ConversationAIState.OFF
            return instagram_state
    except ImportError:
        pass
    try:
        from web_chat.services import ai_state_for_installation

        public_state = ai_state_for_installation(organization, connection)
        if public_state is not None:
            return public_state
    except ImportError:
        pass
    if not is_internal_test_connection(connection):
        return ConversationAIState.OFF
    config = ensure_runtime_config(organization)
    if not config.enabled or config.default_mode == RuntimeMode.OFF:
        return ConversationAIState.OFF
    if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(pk=connection.pk).exists():
        return ConversationAIState.OFF
    if config.default_mode == RuntimeMode.AUTOPILOT_TEST and not _autopilot_environment_allowed():
        return ConversationAIState.SUGGEST
    return config.default_mode


def _autopilot_environment_allowed():
    return bool(
        settings.AI_INTERNAL_TEST_AUTOPILOT
        and (settings.DEBUG or settings.TESTING or settings.E2E_TESTING)
    )


def _policies(organization):
    ensure_runtime_config(organization)
    policies = AIToolPolicy.objects.for_organization(organization).order_by("tool_name")
    return [
        policy for policy in policies
        if operation_allowed(organization=organization, ai=True, tool_name=policy.tool_name)
    ]


def _check_run_limits(config, *, current_run=None):
    today = timezone.localdate()
    month = today.replace(day=1)
    daily_runs = AIRun.objects.for_organization(config.organization).filter(created_at__date=today)
    if current_run is not None:
        daily_runs = daily_runs.exclude(pk=current_run.pk)
    daily = daily_runs.count()
    totals = AIUsageEvent.objects.for_organization(config.organization).filter(month_bucket=month).aggregate(
        input=Sum("input_tokens"), output=Sum("output_tokens")
    )
    if daily >= config.daily_run_limit:
        raise AIRuntimeLimit("daily_run_limit")
    if (totals["input"] or 0) >= config.monthly_input_token_limit:
        raise AIRuntimeLimit("monthly_input_token_limit")
    if (totals["output"] or 0) >= config.monthly_output_token_limit:
        raise AIRuntimeLimit("monthly_output_token_limit")


def _validate_channel(config, conversation):
    public_allowed = False
    try:
        from web_chat.services import is_public_web_chat_connection

        public_allowed = is_public_web_chat_connection(conversation.channel_connection)
    except ImportError:
        pass
    internal_allowed = settings.ENABLE_CRM_TEST_CHANNEL and is_internal_test_connection(
        conversation.channel_connection
    )
    instagram_allowed = conversation.channel_connection.type == "instagram"
    if instagram_allowed:
        from instagram.services import window_eligibility

        instagram_allowed = window_eligibility(conversation).get("state") == "can_reply"
    telegram_allowed = conversation.channel_connection.type == "telegram"
    if telegram_allowed:
        from telegram.services import can_send_telegram

        telegram_allowed = can_send_telegram(conversation)
    gmail_allowed = conversation.channel_connection.type == "gmail"
    if gmail_allowed:
        from gmail_integration.services import can_send_gmail

        gmail_allowed = can_send_gmail(conversation)
    sms_allowed = conversation.channel_connection.type == "sms"
    if sms_allowed:
        from sms.services import can_send_sms

        sms_allowed = can_send_sms(conversation)
    if not internal_allowed and not public_allowed and not instagram_allowed and not telegram_allowed and not gmail_allowed and not sms_allowed:
        raise AIRuntimeUnavailable("internal_test_channel_only")
    if config.allowed_channel_connections.exists() and not config.allowed_channel_connections.filter(
        pk=conversation.channel_connection_id
    ).exists():
        raise AIRuntimeUnavailable("channel_not_allowed")


@transaction.atomic
def create_queued_run(*, message, task_key, mode=None):
    message = (
        Message.objects.select_for_update()
        .select_related("organization", "conversation__contact", "conversation__channel_connection")
        .get(pk=message.pk)
    )
    organization = message.organization
    conversation = message.conversation
    if message.direction != MessageDirection.INBOUND:
        raise AIRuntimeUnavailable("inbound_trigger_required")
    if organization.status not in {OrganizationStatus.TRIAL, OrganizationStatus.ACTIVE}:
        raise AIRuntimeUnavailable("organization_read_only")
    if not operation_allowed(
        organization=organization,
        provider_type="openai",
        channel_connection=conversation.channel_connection,
        ai=True,
        autopilot=bool(mode and "autopilot" in str(mode)),
    ):
        raise AIRuntimeUnavailable("operational_control_active")
    config = ensure_runtime_config(organization)
    config = OrganizationAIRuntimeConfig.objects.select_for_update().get(pk=config.pk)
    if not config.enabled:
        raise AIRuntimeUnavailable("runtime_disabled")
    _validate_channel(config, conversation)
    selected_mode = mode or conversation.ai_state
    if selected_mode not in {
        RuntimeMode.SUGGEST,
        RuntimeMode.AUTOPILOT_TEST,
        RuntimeMode.AUTOPILOT_WEB_CHAT,
        RuntimeMode.AUTOPILOT_INSTAGRAM,
        RuntimeMode.AUTOPILOT_TELEGRAM,
        RuntimeMode.AUTOPILOT_GMAIL,
        RuntimeMode.AUTOPILOT_SMS,
    }:
        raise AIRuntimeUnavailable("conversation_ai_paused")
    if selected_mode == RuntimeMode.AUTOPILOT_TEST and not _autopilot_environment_allowed():
        selected_mode = RuntimeMode.SUGGEST
    if AIHandoff.objects.for_organization(organization).filter(
        conversation=conversation, status__in=[AIHandoffStatus.OPEN, AIHandoffStatus.ACKNOWLEDGED]
    ).exists():
        raise AIRuntimeConflict("active_handoff")
    existing = AIRun.objects.for_organization(organization).filter(task_key=task_key).first()
    if existing:
        return existing, False
    if AIRun.objects.for_organization(organization).filter(
        conversation=conversation,
        status__in=[
            AIRunStatus.QUEUED,
            AIRunStatus.RUNNING,
            AIRunStatus.WAITING_FOR_APPROVAL,
        ],
    ).exists():
        raise AIRuntimeConflict("active_run")
    if AIDraft.objects.for_organization(organization).filter(
        conversation=conversation, status=AIDraftStatus.PENDING
    ).exists():
        raise AIRuntimeConflict("active_run")
    _check_run_limits(config)
    policies = _policies(organization)
    allowed_tools = [item.tool_name for item in policies if item.enabled and item.execution_mode != ToolExecutionMode.DISABLED]
    if "request_human_handoff" not in allowed_tools:
        allowed_tools.append("request_human_handoff")
    try:
        context = build_runtime_context(
            organization=organization, conversation=conversation, allowed_tools=allowed_tools
        )
    except PublishedContextRequired as exc:
        raise AIRuntimeUnavailable("published_ai_context_required") from exc
    prompt, prompt_hash, template_version = build_prompt(context)
    run = AIRun(
        organization=organization,
        conversation=conversation,
        trigger_message=message,
        status=AIRunStatus.QUEUED,
        mode=selected_mode,
        provider=config.provider,
        model=config.model,
        ai_context_revision=context.revision,
        prompt_template_version=template_version,
        prompt_hash=prompt_hash,
        task_key=task_key,
    )
    run.full_clean()
    try:
        run.save()
    except IntegrityError as exc:
        existing = AIRun.objects.for_organization(organization).filter(task_key=task_key).first()
        if existing:
            return existing, False
        raise AIRuntimeConflict("active_run") from exc
    return run, True


def queue_for_inbound_message(message_id):
    message = Message.objects.select_related("conversation", "organization").get(pk=message_id)
    return create_queued_run(message=message, task_key=f"inbound:{message.id}")


def queue_manual_run(*, conversation, actor, request_key):
    if conversation.organization_id != actor.organization_id:
        raise Conversation.DoesNotExist
    now = timezone.now()
    recent_manual = AIRun.objects.for_organization(conversation.organization).filter(
        conversation=conversation,
        task_key__startswith=f"manual:{actor.id}:",
        created_at__gte=now - timedelta(minutes=1),
    ).count()
    if recent_manual >= settings.AI_MANUAL_GENERATION_PER_MINUTE:
        raise AIRuntimeLimit("manual_generation_throttle")
    message = conversation.messages.filter(direction=MessageDirection.INBOUND).order_by("-occurred_at").first()
    if not message:
        raise AIRuntimeUnavailable("inbound_trigger_required")
    return create_queued_run(
        message=message,
        task_key=f"manual:{actor.id}:{request_key}",
        mode=RuntimeMode.SUGGEST,
    )


def _stale_after_human_reply(run):
    return Message.objects.for_organization(run.organization).filter(
        conversation=run.conversation,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.AGENT,
        occurred_at__gt=run.trigger_message.occurred_at,
    ).exists()


@transaction.atomic
def supersede_active_runs(*, conversation, reason):
    now = timezone.now()
    runs = AIRun.objects.select_for_update().for_organization(conversation.organization).filter(
        conversation=conversation,
        status__in=[AIRunStatus.QUEUED, AIRunStatus.RUNNING, AIRunStatus.WAITING_FOR_APPROVAL],
    )
    run_ids = list(runs.values_list("id", flat=True))
    runs.update(
        status=AIRunStatus.SUPERSEDED,
        error_category="stale_context",
        error_code=reason,
        completed_at=now,
    )
    AIDraft.objects.for_organization(conversation.organization).filter(
        conversation=conversation, status=AIDraftStatus.PENDING
    ).update(status=AIDraftStatus.SUPERSEDED, acted_at=now)
    AIToolCall.objects.for_organization(conversation.organization).filter(
        run_id__in=run_ids,
        status__in=[AIToolCallStatus.PROPOSED, AIToolCallStatus.AWAITING_APPROVAL],
    ).update(status=AIToolCallStatus.CANCELLED, completed_at=now)
    return len(run_ids)


def process_run(run_id):
    with transaction.atomic():
        run = (
            AIRun.objects.select_for_update()
            .select_related(
                "organization", "conversation__contact", "conversation__channel_connection",
                "trigger_message", "ai_context_revision"
            )
            .get(pk=run_id)
        )
        if run.status not in {AIRunStatus.QUEUED, AIRunStatus.RUNNING}:
            return run
        conversation = Conversation.objects.select_for_update().get(pk=run.conversation_id)
        if conversation.ai_state not in {
            ConversationAIState.SUGGEST,
            ConversationAIState.AUTOPILOT_TEST,
            ConversationAIState.AUTOPILOT_WEB_CHAT,
            ConversationAIState.AUTOPILOT_INSTAGRAM,
            ConversationAIState.AUTOPILOT_TELEGRAM,
            ConversationAIState.AUTOPILOT_GMAIL,
            ConversationAIState.AUTOPILOT_SMS,
        } or _stale_after_human_reply(run):
            run.status = AIRunStatus.SUPERSEDED
            run.error_category = "stale_context"
            run.error_code = "human_takeover_or_pause"
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "error_category", "error_code", "completed_at"])
            AIUsageEvent.for_run(run)
            return run
        run.status = AIRunStatus.RUNNING
        run.started_at = run.started_at or timezone.now()
        run.save(update_fields=["status", "started_at"])

    config = ensure_runtime_config(run.organization)
    try:
        if not operation_allowed(
            organization=run.organization,
            provider_type="openai",
            channel_connection=run.conversation.channel_connection,
            ai=True,
            autopilot="autopilot" in str(run.mode),
        ):
            raise AIRuntimeUnavailable("operational_control_active")
        _check_run_limits(config, current_run=run)
        policies = _policies(run.organization)
        tools = provider_tools_for(policies)
        context = build_runtime_context(
            organization=run.organization,
            conversation=run.conversation,
            allowed_tools=[item["name"] for item in tools],
        )
        if context.revision.id != run.ai_context_revision_id:
            # A newly published revision supersedes queued work; do not mix versions.
            return _fail_run(run, status=AIRunStatus.SUPERSEDED, category="stale_context", code="context_revision_changed")
        prompt, prompt_hash, _ = build_prompt(context)
        if prompt_hash != run.prompt_hash:
            return _fail_run(run, status=AIRunStatus.SUPERSEDED, category="stale_context", code="prompt_changed")
        provider = provider_for(config)
        result = provider.generate(
            prompt=prompt,
            tools=tools,
            latest_message=context.latest_message,
            max_output_tokens=config.max_output_tokens,
        )
        _apply_provider_trace(run, result)
        if result.tool_calls:
            return _handle_tool_calls(run=run, result=result, policies=policies, provider=provider, prompt=prompt, tools=tools, config=config, context=context)
        return _complete_text(run=run, text=result.text, context=context)
    except PublishedContextRequired:
        return _fail_run(run, category="configuration", code="published_ai_context_required")
    except AIRuntimeLimit as exc:
        return _fail_run(run, category="limit", code=str(exc))
    except AIProviderError as exc:
        try:
            from web_chat.services import is_public_web_chat_connection

            if is_public_web_chat_connection(run.conversation.channel_connection):
                create_handoff(
                    conversation=run.conversation,
                    run=run,
                    reason_code="provider_unavailable",
                    safe_summary="The assistant is unavailable, so a team member needs to continue the Web Chat.",
                    requested_by=HandoffRequestedBy.POLICY,
                )
                run.refresh_from_db()
                return run
        except ImportError:
            pass
        return _fail_run(run, category="provider", code=exc.code)
    except (ToolValidationError, ToolPermissionError) as exc:
        create_handoff(
            conversation=run.conversation,
            run=run,
            reason_code="tool_policy_failure",
            safe_summary="An AI-proposed CRM action failed server validation and needs human review.",
            requested_by=HandoffRequestedBy.POLICY,
        )
        run.refresh_from_db()
        return run
    except AIRuntimeUnavailable as exc:
        if run.conversation.channel_connection.type == "instagram":
            create_handoff(
                conversation=run.conversation,
                run=run,
                reason_code="instagram_send_unavailable",
                safe_summary="Instagram could not accept the governed reply, so a team member needs to continue.",
                requested_by=HandoffRequestedBy.POLICY,
            )
            run.refresh_from_db()
            return run
        if run.conversation.channel_connection.type == "telegram":
            create_handoff(
                conversation=run.conversation,
                run=run,
                reason_code="telegram_send_unavailable",
                safe_summary="Telegram could not accept the governed reply, so a team member needs to continue.",
                requested_by=HandoffRequestedBy.POLICY,
            )
            run.refresh_from_db()
            return run
        if run.conversation.channel_connection.type == "gmail":
            create_handoff(
                conversation=run.conversation,
                run=run,
                reason_code="gmail_send_unavailable",
                safe_summary="Gmail could not accept the governed reply, so a team member needs to continue.",
                requested_by=HandoffRequestedBy.POLICY,
            )
            run.refresh_from_db()
            return run
        return _fail_run(run, category="policy", code=str(exc))
    except Exception:
        logger.exception(
            "ai_run_failed organization_id=%s conversation_id=%s run_id=%s provider=%s model=%s",
            run.organization_id,
            run.conversation_id,
            run.id,
            run.provider,
            run.model,
        )
        return _fail_run(run, category="internal", code="internal_error")


def _apply_provider_trace(run, result):
    run.response_id = result.response_id[:120]
    run.provider_request_id = result.request_id[:120]
    run.input_tokens += result.input_tokens
    run.output_tokens += result.output_tokens
    run.cached_tokens += result.cached_tokens
    run.latency_ms += result.latency_ms
    run.save(update_fields=[
        "response_id", "provider_request_id", "input_tokens", "output_tokens", "cached_tokens", "latency_ms"
    ])


def _handle_tool_calls(*, run, result, policies, provider, prompt, tools, config, context):
    if run.tool_rounds >= config.max_tool_rounds or len(result.tool_calls) > settings.AI_MAX_TOOL_CALLS_PER_RUN:
        create_handoff(
            conversation=run.conversation,
            run=run,
            reason_code="tool_limit",
            safe_summary="The AI reached the configured tool limit and needs human review.",
            requested_by=HandoffRequestedBy.POLICY,
        )
        return run
    by_name = {item.tool_name: item for item in policies}
    outputs = []
    pending = False
    for proposed in result.tool_calls:
        spec = TOOL_REGISTRY.get(proposed.name)
        try:
            if not spec:
                raise ToolValidationError("unknown_tool")
            safe_input = validate_arguments(spec, proposed.arguments)
        except ToolValidationError:
            _record_rejected_call(run, proposed, "invalid_tool_input")
            create_handoff(
                conversation=run.conversation,
                run=run,
                reason_code="invalid_tool_proposal",
                safe_summary="The AI proposed an invalid CRM action and needs human review.",
                requested_by=HandoffRequestedBy.POLICY,
            )
            return run
        policy = by_name.get(proposed.name)
        allowed = spec.always_available or (
            policy and policy.enabled and policy.execution_mode != ToolExecutionMode.DISABLED
        )
        if not allowed:
            _record_rejected_call(run, proposed, "tool_disabled")
            create_handoff(
                conversation=run.conversation,
                run=run,
                reason_code="tool_disabled",
                safe_summary="A requested CRM action is disabled by organization policy.",
                requested_by=HandoffRequestedBy.POLICY,
            )
            return run
        mode = ToolExecutionMode.AUTOMATIC if spec.always_available else policy.execution_mode
        call, _ = AIToolCall.objects.get_or_create(
            organization=run.organization,
            run=run,
            provider_call_id=proposed.call_id,
            defaults={
                "tool_name": proposed.name,
                "input_redacted": safe_input,
                "status": (
                    AIToolCallStatus.AWAITING_APPROVAL
                    if mode == ToolExecutionMode.REQUIRE_APPROVAL
                    else AIToolCallStatus.PROPOSED
                ),
                "idempotency_key": hashlib.sha256(f"{run.id}:{proposed.call_id}:{proposed.name}".encode()).hexdigest(),
                "requires_approval": mode == ToolExecutionMode.REQUIRE_APPROVAL,
            },
        )
        if mode == ToolExecutionMode.REQUIRE_APPROVAL:
            pending = True
            continue
        actor = _automation_actor(run.organization) if spec.mutating else None
        output = execute_tool(call=call, actor=actor)
        outputs.append({"call_id": proposed.call_id, "output": output, "ok": True})
        if spec.name == "request_human_handoff":
            return run
    run.tool_rounds += 1
    if pending:
        run.status = AIRunStatus.WAITING_FOR_APPROVAL
        run.save(update_fields=["status", "tool_rounds"])
        record_activity(
            organization=run.organization,
            event_type="ai.tool_approval_required",
            summary="AI tool proposal requires approval",
            contact=run.conversation.contact,
            conversation=run.conversation,
            metadata={"run_id": str(run.id)},
        )
        return run
    run.save(update_fields=["tool_rounds"])
    next_result = provider.continue_after_tools(
        prompt=prompt,
        tools=tools,
        previous=result,
        tool_outputs=outputs,
        max_output_tokens=config.max_output_tokens,
    )
    _apply_provider_trace(run, next_result)
    if next_result.tool_calls:
        return _handle_tool_calls(
            run=run, result=next_result, policies=policies, provider=provider,
            prompt=prompt, tools=tools, config=config, context=context,
        )
    return _complete_text(run=run, text=next_result.text, context=context)


def _record_rejected_call(run, proposed, code):
    return AIToolCall.objects.get_or_create(
        organization=run.organization,
        run=run,
        provider_call_id=proposed.call_id,
        defaults={
            "tool_name": proposed.name[:80],
            "input_redacted": {},
            "status": AIToolCallStatus.REJECTED,
            "idempotency_key": hashlib.sha256(f"{run.id}:{proposed.call_id}".encode()).hexdigest(),
            "requires_approval": False,
            "error_category": code,
            "completed_at": timezone.now(),
        },
    )[0]


def _complete_text(*, run, text, context):
    snapshot = context.revision.snapshot
    supported = snapshot.get("supported_languages") or [run.organization.default_language]
    default = snapshot.get("default_language") or run.organization.default_language
    language = select_language(context.latest_message, supported, default)
    try:
        safe_text = validate_generated_text(text, language=language, supported_languages=supported)
    except ValueError as exc:
        create_handoff(
            conversation=run.conversation,
            run=run,
            reason_code=str(exc),
            safe_summary="The generated response did not pass deterministic safety checks.",
            requested_by=HandoffRequestedBy.POLICY,
        )
        return run
    if run.mode in {
        RuntimeMode.AUTOPILOT_TEST,
        RuntimeMode.AUTOPILOT_WEB_CHAT,
        RuntimeMode.AUTOPILOT_INSTAGRAM,
        RuntimeMode.AUTOPILOT_TELEGRAM,
        RuntimeMode.AUTOPILOT_GMAIL,
        RuntimeMode.AUTOPILOT_SMS,
    } and _can_autopilot(run):
        ai_message, _ = _create_ai_message(
            run=run,
            body=safe_text,
            client_message_id=f"ai-run:{run.id}",
            metadata={"ai_run_id": str(run.id), "mode": run.mode},
        )
        if ai_message.status == MessageStatus.FAILED:
            return _fail_run(
                run,
                status=AIRunStatus.FAILED,
                category="provider_unavailable",
                code=ai_message.error_code or "send_failed",
            )
        if run.mode == RuntimeMode.AUTOPILOT_TEST:
            run.outcome = AIRunOutcome.SENT_TEST_REPLY
        elif run.mode == RuntimeMode.AUTOPILOT_WEB_CHAT:
            run.outcome = AIRunOutcome.SENT_WEB_CHAT_REPLY
        elif run.mode == RuntimeMode.AUTOPILOT_INSTAGRAM:
            run.outcome = AIRunOutcome.SENT_INSTAGRAM_REPLY
        elif run.mode == RuntimeMode.AUTOPILOT_TELEGRAM:
            run.outcome = AIRunOutcome.SENT_TELEGRAM_REPLY
        elif run.mode == RuntimeMode.AUTOPILOT_SMS:
            run.outcome = AIRunOutcome.SENT_SMS_REPLY
        else:
            run.outcome = AIRunOutcome.SENT_GMAIL_REPLY
    else:
        AIDraft.objects.update_or_create(
            organization=run.organization,
            run=run,
            defaults={"conversation": run.conversation, "body": safe_text, "language": language},
        )
        run.outcome = AIRunOutcome.DRAFT
    run.response_language = language
    run.status = AIRunStatus.COMPLETED
    run.completed_at = timezone.now()
    run.save(update_fields=["response_language", "outcome", "status", "completed_at"])
    AIUsageEvent.for_run(run)
    record_activity(
        organization=run.organization,
        event_type="ai.reply_created",
        summary=(
            "AI draft created"
            if run.outcome == AIRunOutcome.DRAFT
            else "AI internal test reply sent"
            if run.outcome == AIRunOutcome.SENT_TEST_REPLY
            else "AI Web Chat reply sent"
            if run.outcome == AIRunOutcome.SENT_WEB_CHAT_REPLY
            else "AI Instagram reply sent"
            if run.outcome == AIRunOutcome.SENT_INSTAGRAM_REPLY
            else "AI Telegram reply sent"
            if run.outcome == AIRunOutcome.SENT_TELEGRAM_REPLY
            else "AI SMS reply sent"
            if run.outcome == AIRunOutcome.SENT_SMS_REPLY
            else "AI Gmail reply sent"
        ),
        contact=run.conversation.contact,
        conversation=run.conversation,
        metadata={"run_id": str(run.id), "outcome": run.outcome},
    )
    _log_run(run)
    return run


def _can_autopilot(run):
    conversation = Conversation.objects.get(pk=run.conversation_id)
    channel_allowed = bool(
        _autopilot_environment_allowed()
        and is_internal_test_connection(conversation.channel_connection)
        and conversation.ai_state == ConversationAIState.AUTOPILOT_TEST
    )
    try:
        from web_chat.services import web_chat_autopilot_allowed

        if conversation.ai_state == ConversationAIState.AUTOPILOT_WEB_CHAT:
            channel_allowed = web_chat_autopilot_allowed(conversation.channel_connection.web_chat_installation)
    except (ImportError, AttributeError):
        pass
    if conversation.ai_state == ConversationAIState.AUTOPILOT_INSTAGRAM:
        try:
            from instagram.services import instagram_autopilot_allowed

            channel_allowed = instagram_autopilot_allowed(conversation)
        except ImportError:
            channel_allowed = False
    if conversation.ai_state == ConversationAIState.AUTOPILOT_TELEGRAM:
        try:
            from telegram.services import telegram_autopilot_allowed

            channel_allowed = telegram_autopilot_allowed(conversation)
        except ImportError:
            channel_allowed = False
    if conversation.ai_state == ConversationAIState.AUTOPILOT_GMAIL:
        try:
            from gmail_integration.services import gmail_autopilot_allowed

            channel_allowed = gmail_autopilot_allowed(conversation)
        except ImportError:
            channel_allowed = False
    if conversation.ai_state == ConversationAIState.AUTOPILOT_SMS:
        try:
            from sms.services import sms_autopilot_allowed

            channel_allowed = sms_autopilot_allowed(conversation)
        except ImportError:
            channel_allowed = False
    return bool(
        channel_allowed
        and not _stale_after_human_reply(run)
        and not AIHandoff.objects.for_organization(run.organization).filter(
            conversation=conversation, status__in=[AIHandoffStatus.OPEN, AIHandoffStatus.ACKNOWLEDGED]
        ).exists()
        and not AIToolCall.objects.for_organization(run.organization).filter(
            run=run, status=AIToolCallStatus.AWAITING_APPROVAL
        ).exists()
    )


@transaction.atomic
def _create_ai_message(*, run, body, client_message_id, metadata):
    existing = Message.objects.for_organization(run.organization).filter(
        conversation=run.conversation, client_message_id=client_message_id
    ).first()
    if existing:
        return existing, False
    if run.conversation.channel_connection.type == "instagram":
        from instagram.services import send_ai_message

        try:
            return send_ai_message(
                run=run,
                body=body,
                client_message_id=client_message_id,
                metadata=metadata,
            )
        except Exception as exc:
            from instagram.services import InstagramError

            if isinstance(exc, InstagramError):
                raise AIRuntimeUnavailable(exc.code) from exc
            raise
    if run.conversation.channel_connection.type == "telegram":
        from telegram.services import TelegramError, send_ai_message

        try:
            return send_ai_message(run=run, body=body, client_message_id=client_message_id, metadata=metadata)
        except TelegramError as exc:
            raise AIRuntimeUnavailable(exc.code) from exc
    if run.conversation.channel_connection.type == "gmail":
        from gmail_integration.services import GmailError, send_ai_message

        try:
            return send_ai_message(run=run, body=body, client_message_id=client_message_id, metadata=metadata)
        except GmailError as exc:
            raise AIRuntimeUnavailable(exc.code) from exc
    if run.conversation.channel_connection.type == "sms":
        from sms.services import SMSError, send_ai_message

        try:
            return send_ai_message(run=run, body=body, client_message_id=client_message_id, metadata=metadata)
        except SMSError as exc:
            raise AIRuntimeUnavailable(exc.code) from exc
    is_test = settings.ENABLE_CRM_TEST_CHANNEL and is_internal_test_connection(run.conversation.channel_connection)
    is_public = False
    try:
        from web_chat.services import can_send_public_web_chat

        is_public = can_send_public_web_chat(run.conversation)
    except ImportError:
        pass
    if not is_test and not is_public:
        raise AIRuntimeUnavailable("external_send_blocked")
    now = timezone.now()
    message = Message(
        organization=run.organization,
        conversation=run.conversation,
        channel_connection=run.conversation.channel_connection,
        direction=MessageDirection.OUTBOUND,
        sender_type=MessageSenderType.AI,
        client_message_id=client_message_id,
        content_type=MessageContentType.TEXT,
        body=body,
        status=MessageStatus.SENT,
        metadata={"test_data": is_test, "ai_generated": True, **metadata},
        occurred_at=now,
    )
    message.full_clean()
    message.save()
    Conversation.objects.filter(pk=run.conversation_id).update(last_message_at=now, last_outbound_at=now)
    if is_public:
        from web_chat.services import publish_message_event

        transaction.on_commit(lambda: publish_message_event(message))
    return message, True


@transaction.atomic
def act_on_draft(*, draft, actor, action, body=None, rejection_reason=""):
    draft = AIDraft.objects.select_for_update().select_related("run__trigger_message", "conversation__channel_connection").get(pk=draft.pk)
    if draft.organization_id != actor.organization_id:
        raise AIDraft.DoesNotExist
    if draft.status != AIDraftStatus.PENDING:
        raise AIRuntimeConflict("stale_draft")
    if _stale_after_human_reply(draft.run):
        draft.status = AIDraftStatus.SUPERSEDED
        draft.acted_at = timezone.now()
        draft.save(update_fields=["status", "acted_at", "updated_at"])
        raise AIRuntimeConflict("stale_draft")
    now = timezone.now()
    if action == "reject":
        draft.status = AIDraftStatus.REJECTED
        draft.rejected_by = actor
        draft.rejection_reason = rejection_reason.strip()[:500]
        draft.acted_at = now
        draft.save(update_fields=["status", "rejected_by", "rejection_reason", "acted_at", "updated_at"])
        event = "ai.draft_rejected"
    else:
        final_body = draft.body if action == "approve" else (body or "").strip()
        if not final_body or "<" in final_body or ">" in final_body:
            raise AIRuntimeConflict("invalid_draft_body")
        _create_ai_message(
            run=draft.run,
            body=final_body,
            client_message_id=f"ai-draft:{draft.id}",
            metadata={"draft_id": str(draft.id), "approved_by": str(actor.id), "edited": action == "edit"},
        )
        draft.status = AIDraftStatus.APPROVED if action == "approve" else AIDraftStatus.EDITED_AND_SENT
        draft.approved_by = actor
        draft.body = final_body
        draft.acted_at = now
        draft.save(update_fields=["status", "approved_by", "body", "acted_at", "updated_at"])
        event = "ai.draft_approved" if action == "approve" else "ai.draft_edited_and_sent"
    record_activity(
        organization=draft.organization,
        actor_membership=actor,
        event_type=event,
        summary=event.replace("ai.", "AI ").replace("_", " "),
        contact=draft.conversation.contact,
        conversation=draft.conversation,
        metadata={"draft_id": str(draft.id), "run_id": str(draft.run_id)},
    )
    return draft


@transaction.atomic
def approve_tool_call(*, call, actor):
    call = AIToolCall.objects.select_for_update().select_related("run__conversation__contact").get(pk=call.pk)
    if call.organization_id != actor.organization_id:
        raise AIToolCall.DoesNotExist
    if call.status != AIToolCallStatus.AWAITING_APPROVAL:
        raise AIRuntimeConflict("stale_tool_call")
    if _stale_after_human_reply(call.run):
        call.status = AIToolCallStatus.CANCELLED
        call.completed_at = timezone.now()
        call.save(update_fields=["status", "completed_at"])
        raise AIRuntimeConflict("stale_tool_call")
    call.status = AIToolCallStatus.APPROVED
    call.approved_by = actor
    call.approved_at = timezone.now()
    call.save(update_fields=["status", "approved_by", "approved_at"])
    try:
        execute_tool(call=call, actor=actor)
    except (ToolValidationError, ToolPermissionError) as exc:
        call.status = AIToolCallStatus.FAILED
        call.error_category = str(exc)[:60]
        call.completed_at = timezone.now()
        call.save(update_fields=["status", "error_category", "completed_at"])
        create_handoff(
            conversation=call.run.conversation,
            run=call.run,
            reason_code="tool_execution_failed",
            safe_summary="An approved CRM action failed validation and needs human review.",
            requested_by=HandoffRequestedBy.POLICY,
        )
        return call
    if not call.run.tool_calls.filter(status=AIToolCallStatus.AWAITING_APPROVAL).exists():
        _draft_after_approved_tools(call.run)
    return call


def reject_tool_call(*, call, actor):
    with transaction.atomic():
        call = AIToolCall.objects.select_for_update().select_related("run__conversation__contact").get(pk=call.pk)
        if call.organization_id != actor.organization_id:
            raise AIToolCall.DoesNotExist
        if call.status != AIToolCallStatus.AWAITING_APPROVAL:
            raise AIRuntimeConflict("stale_tool_call")
        call.status = AIToolCallStatus.REJECTED
        call.approved_by = actor
        call.completed_at = timezone.now()
        call.error_category = "rejected_by_human"
        call.save(update_fields=["status", "approved_by", "completed_at", "error_category"])
        create_handoff(
            conversation=call.run.conversation,
            run=call.run,
            reason_code="tool_rejected",
            safe_summary="A proposed CRM action was rejected and the conversation needs human review.",
            requested_by=HandoffRequestedBy.POLICY,
        )
    return call


def _draft_after_approved_tools(run):
    if run.status != AIRunStatus.WAITING_FOR_APPROVAL:
        return run
    successful = list(run.tool_calls.filter(status=AIToolCallStatus.SUCCEEDED).values_list("tool_name", flat=True))
    text = "Approved CRM action completed: " + ", ".join(successful) + ". A team member can now continue the conversation."
    AIDraft.objects.update_or_create(
        organization=run.organization,
        run=run,
        defaults={"conversation": run.conversation, "body": text, "language": run.organization.default_language},
    )
    run.status = AIRunStatus.COMPLETED
    run.outcome = AIRunOutcome.DRAFT
    run.response_language = run.organization.default_language
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "outcome", "response_language", "completed_at"])
    AIUsageEvent.for_run(run)
    return run


@transaction.atomic
def create_handoff(*, conversation, run, reason_code, safe_summary, requested_by):
    existing = AIHandoff.objects.select_for_update().for_organization(conversation.organization).filter(
        conversation=conversation, status__in=[AIHandoffStatus.OPEN, AIHandoffStatus.ACKNOWLEDGED]
    ).first()
    if existing:
        if run and run.status not in {AIRunStatus.HANDOFF, AIRunStatus.COMPLETED}:
            run.status = AIRunStatus.HANDOFF
            run.outcome = AIRunOutcome.HANDOFF
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "outcome", "completed_at"])
            AIUsageEvent.for_run(run)
        return existing
    handoff = AIHandoff(
        organization=conversation.organization,
        conversation=conversation,
        run=run,
        reason_code=reason_code[:80],
        safe_summary=safe_summary[:2000],
        requested_by=requested_by,
    )
    handoff.full_clean()
    handoff.save()
    Conversation.objects.filter(pk=conversation.pk).update(
        ai_state=ConversationAIState.HANDOFF_REQUIRED,
        ai_state_updated_at=timezone.now(),
        handoff_reason=reason_code[:500],
    )
    if run:
        run.status = AIRunStatus.HANDOFF
        run.outcome = AIRunOutcome.HANDOFF
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "outcome", "completed_at"])
        AIUsageEvent.for_run(run)
    add_system_message(
        conversation=conversation,
        membership=None,
        body=f"AI handoff requested: {reason_code}",
        event_type="ai.handoff_requested",
    )
    _log_run(run) if run else None
    return handoff


@transaction.atomic
def update_handoff(*, handoff, actor, action, assigned_membership=None):
    handoff = AIHandoff.objects.select_for_update().select_related("conversation").get(pk=handoff.pk)
    if handoff.organization_id != actor.organization_id:
        raise AIHandoff.DoesNotExist
    now = timezone.now()
    if action == "acknowledge":
        if handoff.status != AIHandoffStatus.OPEN:
            raise AIRuntimeConflict("stale_handoff")
        handoff.status = AIHandoffStatus.ACKNOWLEDGED
        handoff.acknowledged_at = now
    elif action == "assign":
        if not assigned_membership or assigned_membership.organization_id != handoff.organization_id:
            raise AIRuntimeConflict("invalid_assignment")
        handoff.assigned_membership = assigned_membership
    elif action == "resolve":
        if handoff.status == AIHandoffStatus.RESOLVED:
            raise AIRuntimeConflict("stale_handoff")
        handoff.status = AIHandoffStatus.RESOLVED
        handoff.resolved_at = now
    else:
        raise AIRuntimeConflict("invalid_handoff_action")
    handoff.save()
    record_activity(
        organization=handoff.organization,
        actor_membership=actor,
        event_type=f"ai.handoff_{action}d",
        summary=f"AI handoff {action}d",
        contact=handoff.conversation.contact,
        conversation=handoff.conversation,
        metadata={"handoff_id": str(handoff.id)},
    )
    return handoff


@transaction.atomic
def set_conversation_ai_state(*, conversation, actor, state):
    conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    if conversation.organization_id != actor.organization_id:
        raise Conversation.DoesNotExist
    if state not in {
        ConversationAIState.OFF,
        ConversationAIState.SUGGEST,
        ConversationAIState.AUTOPILOT_TEST,
        ConversationAIState.AUTOPILOT_WEB_CHAT,
        ConversationAIState.AUTOPILOT_INSTAGRAM,
        ConversationAIState.AUTOPILOT_TELEGRAM,
        ConversationAIState.AUTOPILOT_GMAIL,
        ConversationAIState.AUTOPILOT_SMS,
    }:
        raise AIRuntimeConflict("invalid_ai_state")
    config = ensure_runtime_config(conversation.organization)
    if state != ConversationAIState.OFF:
        if not config.enabled:
            raise AIRuntimeUnavailable("runtime_disabled")
        _validate_channel(config, conversation)
    if state == ConversationAIState.AUTOPILOT_TEST and not _autopilot_environment_allowed():
        raise AIRuntimeUnavailable("autopilot_not_enabled")
    if state == ConversationAIState.AUTOPILOT_WEB_CHAT:
        try:
            from web_chat.services import web_chat_autopilot_allowed

            if not web_chat_autopilot_allowed(conversation.channel_connection.web_chat_installation):
                raise AIRuntimeUnavailable("autopilot_not_enabled")
        except AttributeError as exc:
            raise AIRuntimeUnavailable("autopilot_not_enabled") from exc
    if state == ConversationAIState.AUTOPILOT_INSTAGRAM:
        try:
            from instagram.services import instagram_autopilot_allowed

            if not instagram_autopilot_allowed(conversation):
                raise AIRuntimeUnavailable("autopilot_not_enabled")
        except ImportError as exc:
            raise AIRuntimeUnavailable("autopilot_not_enabled") from exc
    if state == ConversationAIState.AUTOPILOT_TELEGRAM:
        try:
            from telegram.services import telegram_autopilot_configured

            if not telegram_autopilot_configured(conversation):
                raise AIRuntimeUnavailable("autopilot_not_enabled")
        except ImportError as exc:
            raise AIRuntimeUnavailable("autopilot_not_enabled") from exc
    if state == ConversationAIState.AUTOPILOT_GMAIL:
        try:
            from gmail_integration.services import gmail_autopilot_allowed

            if not gmail_autopilot_allowed(conversation):
                raise AIRuntimeUnavailable("autopilot_not_enabled")
        except ImportError as exc:
            raise AIRuntimeUnavailable("autopilot_not_enabled") from exc
    if state == ConversationAIState.AUTOPILOT_SMS:
        try:
            from sms.services import sms_autopilot_allowed

            conversation.ai_state = state
            if not sms_autopilot_allowed(conversation):
                raise AIRuntimeUnavailable("autopilot_not_enabled")
        except ImportError as exc:
            raise AIRuntimeUnavailable("autopilot_not_enabled") from exc
    conversation.ai_state = state
    conversation.ai_state_updated_at = timezone.now()
    conversation.handoff_reason = "" if state != ConversationAIState.HANDOFF_REQUIRED else conversation.handoff_reason
    conversation.save(update_fields=["ai_state", "ai_state_updated_at", "handoff_reason", "updated_at"])
    if state == ConversationAIState.OFF:
        supersede_active_runs(conversation=conversation, reason="paused_by_user")
    record_activity(
        organization=conversation.organization,
        actor_membership=actor,
        event_type="ai.conversation_state_changed",
        summary=f"Conversation AI state changed to {state}",
        contact=conversation.contact,
        conversation=conversation,
        metadata={"ai_state": state},
    )
    return conversation


def _automation_actor(organization):
    actor = OrganizationMembership.objects.filter(
        organization=organization,
        status="active",
        role__in=[OrganizationMembershipRole.OWNER, OrganizationMembershipRole.ADMIN],
    ).order_by("created_at").first()
    if not actor:
        raise ToolPermissionError("automation_actor_unavailable")
    return actor


def _fail_run(run, *, status=AIRunStatus.FAILED, category, code):
    run.status = status
    run.outcome = AIRunOutcome.FAILED if status == AIRunStatus.FAILED else run.outcome
    run.error_category = category[:60]
    run.error_code = code[:80]
    run.completed_at = timezone.now()
    run.save(update_fields=["status", "outcome", "error_category", "error_code", "completed_at"])
    AIUsageEvent.for_run(run)
    _log_run(run)
    return run


def _log_run(run):
    logger.info(
        "ai_run organization_id=%s conversation_id=%s run_id=%s provider=%s model=%s status=%s duration_ms=%s input_tokens=%s output_tokens=%s",
        run.organization_id,
        run.conversation_id,
        run.id,
        run.provider,
        run.model,
        run.status,
        run.latency_ms,
        run.input_tokens,
        run.output_tokens,
    )
