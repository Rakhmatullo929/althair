import { describe, expect, it } from "vitest";
import type { VoiceCall, VoiceConnection } from "@workspace/api-client";
import {
  formatCallDuration,
  transcriptVisible,
  voiceNeedsAttention,
} from "@/lib/voice";

describe("Voice UI policy", () => {
  it("formats durations and flags provider/worker health", () => {
    expect(formatCallDuration(125)).toBe("2:05");
    const connection = {
      status: "connected",
      last_error_code: "",
      health: { realtime_ready: true, worker_ready: true },
    } as VoiceConnection;
    expect(voiceNeedsAttention(connection)).toBe(false);
    connection.health.worker_ready = false;
    expect(voiceNeedsAttention(connection)).toBe(true);
  });

  it("never exposes transcript when storage is forbidden", () => {
    const call = {
      transcript_storage_allowed: false,
      transcript: [{ text: "ephemeral" }],
    } as VoiceCall;
    expect(transcriptVisible(call)).toBe(false);
    call.transcript_storage_allowed = true;
    expect(transcriptVisible(call)).toBe(true);
  });
});
