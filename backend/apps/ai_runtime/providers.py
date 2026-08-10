from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings


@dataclass(frozen=True)
class ProviderToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ProviderResult:
    response_id: str
    request_id: str
    text: str = ""
    tool_calls: list[ProviderToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: int = 0
    continuation_items: list[dict[str, Any]] = field(default_factory=list, repr=False)


class AIProviderError(Exception):
    def __init__(self, code: str, *, transient: bool = False):
        super().__init__(code)
        self.code = code
        self.transient = transient


class BaseAIProvider:
    name = "base"

    def generate(self, *, prompt: str, tools: list[dict], latest_message: str, max_output_tokens: int) -> ProviderResult:
        raise NotImplementedError

    def continue_after_tools(
        self,
        *,
        prompt: str,
        tools: list[dict],
        previous: ProviderResult,
        tool_outputs: list[dict],
        max_output_tokens: int,
    ) -> ProviderResult:
        raise NotImplementedError


class FakeAIProvider(BaseAIProvider):
    """Deterministic, body-safe provider used by CI and local development."""

    name = "fake"

    def generate(self, *, prompt: str, tools: list[dict], latest_message: str, max_output_tokens: int) -> ProviderResult:
        started = time.monotonic()
        lowered = latest_message.casefold()
        if "[[fake:provider_error]]" in lowered:
            raise AIProviderError("fake_provider_error")
        allowed = {tool["name"] for tool in tools}
        call = self._tool_call(lowered, latest_message, allowed)
        language = _detect_language(latest_message)
        if call:
            text = ""
            calls = [call]
        else:
            calls = []
            text = _fake_reply(language, lowered)
        return ProviderResult(
            response_id=f"fake-response-{uuid.uuid4().hex}",
            request_id=f"fake-request-{uuid.uuid4().hex}",
            text=text[:max_output_tokens * 4],
            tool_calls=calls,
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, (len(text) + 3) // 4),
            latency_ms=max(1, int((time.monotonic() - started) * 1000)),
        )

    def continue_after_tools(
        self, *, prompt: str, tools: list[dict], previous: ProviderResult, tool_outputs: list[dict], max_output_tokens: int
    ) -> ProviderResult:
        failed = any(not item.get("ok", False) for item in tool_outputs)
        text = (
            "I could not complete that action safely, so a team member will help."
            if failed
            else "The requested CRM action was completed successfully. A team member can review the recorded result."
        )
        return ProviderResult(
            response_id=f"fake-response-{uuid.uuid4().hex}",
            request_id=f"fake-request-{uuid.uuid4().hex}",
            text=text[:max_output_tokens * 4],
            input_tokens=max(1, len(prompt) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=1,
        )

    def _tool_call(self, lowered: str, raw: str, allowed: set[str]):
        if any(token in lowered for token in ("human", "человек", "operator", "менеджер", "complaint", "жалоб")):
            return _call("request_human_handoff", {"reason_code": "customer_request", "safe_summary": "Customer requested human assistance."}, allowed)
        if any(token in lowered for token in ("urgent", "emergency", "диагноз", "medical", "юрист", "refund", "payment", "book ", "booking", "заказ")):
            return _call("request_human_handoff", {"reason_code": "unsupported_or_high_risk", "safe_summary": "Request needs a qualified human because it is unsupported or high risk."}, allowed)
        if any(token in lowered for token in (
            "system prompt", "api key", "secret", "ignore previous", "другая компания",
            "other tenant", "another tenant", "another company",
        )):
            return _call("request_human_handoff", {"reason_code": "policy_conflict", "safe_summary": "Customer message attempted to override platform or tenant safety policy."}, allowed)
        name_match = re.search(r"(?:name is|name to|имя на|меня зовут)\s+([\w\- 'А-Яа-яЁёЎўҚқҒғҲҳ]{2,80})", raw, re.IGNORECASE)
        if name_match:
            return _call_or_policy("update_contact_name", {"display_name": name_match.group(1).strip()}, allowed)
        if "create lead" in lowered or "создай лид" in lowered:
            return _call_or_policy("create_lead", {"title": "AI-qualified inquiry", "description": "Lead requested during the conversation."}, allowed)
        if any(token in lowered for token in ("follow up", "remind", "напомни", "eslat")):
            return _call_or_policy("create_follow_up_task", {"title": "Follow up on AI conversation", "due_in_hours": 24}, allowed)
        if any(token in lowered for token in ("working hours", "open today", "часы работы", "qachon ochiq")):
            return _call("list_active_branches", {}, allowed)
        return None


def _call(name: str, arguments: dict, allowed: set[str]):
    if name not in allowed:
        return None
    return ProviderToolCall(call_id=f"fake-call-{uuid.uuid4().hex}", name=name, arguments=arguments)


def _call_or_policy(name: str, arguments: dict, allowed: set[str]):
    call = _call(name, arguments, allowed)
    if call:
        return call
    return _call(
        "request_human_handoff",
        {
            "reason_code": "tool_disabled",
            "safe_summary": "The requested CRM action is not enabled by organization policy.",
        },
        allowed,
    )


def _detect_language(text: str) -> str:
    lowered = text.casefold()
    if any(char in lowered for char in "ўқғҳ") or any(word in lowered for word in ("salom", "iltimos", "qachon", "eslat")):
        return "uz"
    if re.search(r"[а-яё]", lowered):
        return "ru"
    return "en"


def _fake_reply(language: str, lowered: str) -> str:
    if language == "uz":
        return "Savolingizni qabul qildim. Nashr qilingan kompaniya ma'lumotlariga tayangan holda yordam beraman."
    if language == "ru":
        return "Я получил ваш вопрос. Отвечу только на основе опубликованной информации компании."
    return "I received your question. I will answer only from the company's published information."


class OpenAIResponsesProvider(BaseAIProvider):
    name = "openai"

    def __init__(self, *, model: str, timeout_seconds: int):
        if not settings.AI_RUNTIME_ENABLE_REAL_OPENAI:
            raise AIProviderError("real_openai_disabled")
        if not settings.OPENAI_API_KEY:
            raise AIProviderError("openai_key_missing")
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(
            api_key=settings.OPENAI_API_KEY,
            timeout=timeout_seconds,
            max_retries=settings.OPENAI_MAX_RETRIES,
        )

    def generate(self, *, prompt: str, tools: list[dict], latest_message: str, max_output_tokens: int) -> ProviderResult:
        return self._create(
            input_items=[{"role": "user", "content": prompt}],
            tools=tools,
            max_output_tokens=max_output_tokens,
        )

    def continue_after_tools(
        self, *, prompt: str, tools: list[dict], previous: ProviderResult, tool_outputs: list[dict], max_output_tokens: int
    ) -> ProviderResult:
        outputs = [
            {
                "type": "function_call_output",
                "call_id": item["call_id"],
                "output": json.dumps(item["output"], ensure_ascii=False, separators=(",", ":")),
            }
            for item in tool_outputs
        ]
        return self._create(
            input_items=[{"role": "user", "content": prompt}, *previous.continuation_items, *outputs],
            tools=tools,
            max_output_tokens=max_output_tokens,
        )

    def _create(self, *, input_items: list[dict], tools: list[dict], max_output_tokens: int):
        started = time.monotonic()
        try:
            response = self.client.responses.create(
                model=self.model,
                input=input_items,
                tools=tools,
                max_output_tokens=max_output_tokens,
                store=False,
            )
        except Exception as exc:
            name = exc.__class__.__name__.lower()
            transient = any(part in name for part in ("timeout", "ratelimit", "connection", "server"))
            raise AIProviderError("provider_transient" if transient else "provider_error", transient=transient) from exc
        calls = []
        continuation = []
        for item in response.output:
            if getattr(item, "type", "") != "function_call":
                continue
            try:
                arguments = json.loads(item.arguments)
            except (TypeError, json.JSONDecodeError) as exc:
                raise AIProviderError("invalid_tool_arguments") from exc
            calls.append(ProviderToolCall(call_id=item.call_id, name=item.name, arguments=arguments))
            continuation.append({
                "type": "function_call",
                "call_id": item.call_id,
                "name": item.name,
                "arguments": item.arguments,
            })
        usage = getattr(response, "usage", None)
        details = getattr(usage, "input_tokens_details", None)
        return ProviderResult(
            response_id=response.id,
            request_id=getattr(response, "_request_id", "") or "",
            text=response.output_text or "",
            tool_calls=calls,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cached_tokens=getattr(details, "cached_tokens", 0) or 0,
            latency_ms=max(1, int((time.monotonic() - started) * 1000)),
            continuation_items=continuation,
        )


def provider_for(config):
    if config.provider == "fake":
        return FakeAIProvider()
    if config.provider == "openai":
        return OpenAIResponsesProvider(model=config.model, timeout_seconds=config.timeout_seconds)
    raise AIProviderError("unknown_provider")
