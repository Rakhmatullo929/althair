from __future__ import annotations

import asyncio
import json
import signal
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.db import connections, transaction
from django.utils import timezone

from voice.models import VoiceCall, VoiceCallStatus, VoiceControllerJob
from voice.providers import VoiceProviderError, realtime_provider_for
from voice.services import (
    VoiceError,
    create_callback_handoff,
    execute_voice_tool,
    finalize_call,
    human_takeover,
    record_transcript_consent,
    request_voice_transfer,
    safe_code,
    store_final_transcript,
)


def claim_next_job(worker_id: str):
    with transaction.atomic():
        now = timezone.now()
        job = (
            VoiceControllerJob.objects.select_for_update(skip_locked=True)
            .select_related("call__voice_connection")
            .filter(status="pending", available_at__lte=now)
            .order_by("available_at", "created_at")
            .first()
        )
        if not job:
            return None
        if not cache.add(f"voice:call-lock:{job.call_id}", worker_id, timeout=settings.VOICE_MAX_CALL_SECONDS + 60):
            return None
        job.status = "running"
        job.attempt_count += 1
        job.worker_id = worker_id[:120]
        job.locked_until = now + timedelta(seconds=settings.VOICE_MAX_CALL_SECONDS + 60)
        job.save(update_fields=["status", "attempt_count", "worker_id", "locked_until", "updated_at"])
        return job


def release_job(job_id, *, completed: bool, error: str = ""):
    with transaction.atomic():
        job = VoiceControllerJob.objects.select_for_update().get(pk=job_id)
        cache.delete(f"voice:call-lock:{job.call_id}")
        if completed:
            job.status = "completed"
        elif job.attempt_count < settings.VOICE_CONTROLLER_MAX_ATTEMPTS:
            job.status = "pending"
            job.available_at = timezone.now() + timedelta(seconds=2 ** job.attempt_count)
        else:
            job.status = "failed"
        job.last_error_code = safe_code(error) if error else ""
        job.locked_until = None
        job.worker_id = ""
        job.save()


class VoiceRealtimeController:
    def __init__(self, *, call_id, events: list[dict] | None = None):
        self.call = VoiceCall.objects.select_related(
            "organization", "voice_connection__connected_by", "conversation", "contact"
        ).get(pk=call_id)
        self.provider = realtime_provider_for(self.call.voice_connection, events=events)
        self.ephemeral_segments: list[dict] = []
        self.usage: dict = {}

    async def _persist_segment(self, event: dict, speaker: str):
        text = str(event.get("transcript") or event.get("text") or "").strip()
        if not text:
            return
        refreshed = await sync_to_async(VoiceCall.objects.get)(pk=self.call.pk)
        if refreshed.transcript_storage_allowed:
            await sync_to_async(store_final_transcript)(
                call=refreshed, speaker=speaker, text=text,
                language=str(event.get("language") or refreshed.selected_language),
                start_ms=event.get("start_ms"), end_ms=event.get("end_ms"),
            )
        else:
            self.ephemeral_segments.append({"speaker": speaker, "text": text[:4000]})
            self.ephemeral_segments = self.ephemeral_segments[-20:]

    async def _tool(self, event: dict):
        name = str(event.get("name") or "")
        provider_call_id = str(event.get("call_id") or event.get("item_id") or "")
        arguments = event.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                arguments = {}
        try:
            result = await sync_to_async(execute_voice_tool)(
                call=self.call,
                provider_call_id=provider_call_id,
                tool_name=name,
                arguments=arguments,
                confirmation_marker=str(event.get("confirmation_marker") or ""),
            )
        except Exception as exc:
            result = {"status": "failed", "error": safe_code(getattr(exc, "code", type(exc).__name__))}
        await self.provider.send(
            call_id=self.call.provider_call_id,
            event={
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": provider_call_id, "output": json.dumps(result)},
            },
        )
        await self.provider.send(call_id=self.call.provider_call_id, event={"type": "response.create"})

    async def handle(self, event: dict):
        event_type = str(event.get("type") or "")
        if event_type in {"conversation.item.input_audio_transcription.completed", "voice.caller_transcript.final"}:
            await self._persist_segment(event, "caller")
        elif event_type in {"response.output_audio_transcript.done", "voice.assistant_transcript.final"}:
            await self._persist_segment(event, "assistant")
        elif event_type == "input_audio_buffer.speech_started":
            await self.provider.send(call_id=self.call.provider_call_id, event={"type": "response.cancel"})
            await sync_to_async(VoiceCall.objects.filter(pk=self.call.pk).update)(
                interruption_count=__import__("django.db.models").db.models.F("interruption_count") + 1
            )
        elif event_type in {"response.function_call_arguments.done", "voice.tool_call"}:
            await self._tool(event)
        elif event_type == "voice.transcript_consent":
            await sync_to_async(record_transcript_consent)(self.call, granted=bool(event.get("granted")))
        elif event_type == "voice.language":
            language = str(event.get("language") or "")[:2]
            if language in self.call.voice_connection.supported_languages:
                await sync_to_async(VoiceCall.objects.filter(pk=self.call.pk).update)(selected_language=language)
        elif event_type == "voice.unclear":
            updated = await sync_to_async(VoiceCall.objects.filter(pk=self.call.pk).update)(
                unclear_turn_count=__import__("django.db.models").db.models.F("unclear_turn_count") + 1
            )
            refreshed = await sync_to_async(VoiceCall.objects.get)(pk=self.call.pk)
            if updated and refreshed.unclear_turn_count >= 3:
                await sync_to_async(create_callback_handoff)(refreshed, "repeated_unclear_audio")
                await sync_to_async(finalize_call)(refreshed, outcome="callback_requested", hangup_actor="ai")
                self.call.ai_control_active = False
        elif event_type == "voice.max_duration":
            raise TimeoutError("max_duration")
        elif event_type == "voice.transfer":
            await sync_to_async(request_voice_transfer)(
                call=self.call,
                destination_key=str(event.get("destination_key") or ""),
                idempotency_key=f"voice:{self.call.id}:transfer:{event.get('id') or event.get('destination_key')}",
            )
        elif event_type == "voice.human_takeover":
            await sync_to_async(human_takeover)(self.call, membership=self.call.voice_connection.connected_by)
            self.call.ai_control_active = False
        elif event_type == "response.done":
            usage = event.get("response", {}).get("usage", {}) if isinstance(event.get("response"), dict) else {}
            self.usage.update(usage if isinstance(usage, dict) else {})
        elif event_type in {"voice.completed", "call.completed"}:
            await sync_to_async(finalize_call)(self.call, outcome=str(event.get("outcome") or "answered"), usage=self.usage)
            self.call.ai_control_active = False
        elif event_type in {"voice.provider_disconnect", "error"}:
            raise VoiceProviderError("realtime_provider_disconnect", transient=False)

    async def run(self):
        try:
            await sync_to_async(VoiceCall.objects.filter(pk=self.call.pk).update)(
                status=VoiceCallStatus.ACTIVE, answered_at=self.call.answered_at or timezone.now()
            )
            async with asyncio.timeout(self.call.voice_connection.max_call_seconds):
                async for event in self.provider.events(call_id=self.call.provider_call_id):
                    await self.handle(event)
                    self.call = await sync_to_async(
                        VoiceCall.objects.select_related("voice_connection__connected_by").get
                    )(pk=self.call.pk)
                    if not self.call.ai_control_active or self.call.ended_at:
                        break
            refreshed = await sync_to_async(VoiceCall.objects.get)(pk=self.call.pk)
            if not refreshed.ended_at and refreshed.status != VoiceCallStatus.TRANSFERRED:
                await sync_to_async(finalize_call)(
                    refreshed, outcome="answered", hangup_actor="caller", usage=self.usage
                )
        finally:
            # Async tests and short-lived fake controllers must not retain a
            # PostgreSQL connection in asgiref's thread-sensitive executor.
            await sync_to_async(connections.close_all)()


async def run_worker(*, worker_id: str, once=False, stop_event: asyncio.Event | None = None):
    stop_event = stop_event or asyncio.Event()
    while not stop_event.is_set():
        await sync_to_async(cache.set)("voice:worker:heartbeat", timezone.now().isoformat(), 30)
        job = await sync_to_async(claim_next_job)(worker_id)
        if not job:
            if once:
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=1)
            except TimeoutError:
                continue
            continue
        try:
            await VoiceRealtimeController(call_id=job.call_id).run()
            await sync_to_async(release_job)(job.id, completed=True)
        except TimeoutError:
            call = await sync_to_async(VoiceCall.objects.get)(pk=job.call_id)
            await sync_to_async(finalize_call)(call, outcome="failed", hangup_actor="system", error="max_duration")
            await sync_to_async(release_job)(job.id, completed=True, error="max_duration")
        except Exception as exc:
            call = await sync_to_async(VoiceCall.objects.get)(pk=job.call_id)
            await sync_to_async(finalize_call)(
                call, outcome="failed", hangup_actor="provider", error=safe_code(getattr(exc, "code", type(exc).__name__))
            )
            await sync_to_async(release_job)(job.id, completed=False, error=safe_code(getattr(exc, "code", type(exc).__name__)))
        if once:
            return
