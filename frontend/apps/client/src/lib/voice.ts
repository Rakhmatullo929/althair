import type { VoiceCall, VoiceConnection } from "@workspace/api-client";

export function voiceNeedsAttention(connection: VoiceConnection) {
  return (
    connection.status !== "connected" ||
    !connection.health.realtime_ready ||
    !connection.health.worker_ready ||
    Boolean(connection.last_error_code)
  );
}

export function formatCallDuration(seconds: number) {
  const safe = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(safe / 60);
  return `${minutes}:${String(safe % 60).padStart(2, "0")}`;
}

export function transcriptVisible(call: VoiceCall) {
  return call.transcript_storage_allowed && call.transcript.length > 0;
}
