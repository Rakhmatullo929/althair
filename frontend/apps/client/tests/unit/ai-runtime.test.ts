import type { AIRun, Conversation } from "@workspace/api-client";
import { describe, expect, it } from "vitest";
import {
  aiControlState,
  aiQueryKeys,
  isStaleAIConflict,
  safeRunTrace,
} from "@/lib/ai-runtime";

const conversation = (aiState: Conversation["ai_state"]) => ({
  ai_state: aiState,
});

describe("AI runtime client safety", () => {
  it("isolates every AI cache root by organization", () => {
    expect(aiQueryKeys.config("org-a")).not.toEqual(
      aiQueryKeys.config("org-b"),
    );
    expect(aiQueryKeys.conversation("org-a", "same")).not.toEqual(
      aiQueryKeys.conversation("org-b", "same"),
    );
  });

  it("lets agents pause and generate but never resume or approve tools", () => {
    const active = aiControlState(conversation("suggest"), "agent", "active");
    expect(active.canPause).toBe(true);
    expect(active.canGenerate).toBe(true);
    expect(active.canResume).toBe(false);
    expect(active.canApproveTool).toBe(false);
  });

  it("lets managers resume paused AI and approve tools", () => {
    const paused = aiControlState(
      conversation("paused_by_human"),
      "manager",
      "active",
    );
    expect(paused.canResume).toBe(true);
    expect(paused.canApproveTool).toBe(true);
  });

  it("makes every AI control read-only for a suspended organization", () => {
    const state = aiControlState(conversation("suggest"), "owner", "suspended");
    expect(state).toMatchObject({
      canPause: false,
      canResume: false,
      canGenerate: false,
      canApproveTool: false,
      readOnly: true,
    });
  });

  it("recognizes stale draft, tool, and active-run conflicts", () => {
    expect(isStaleAIConflict("stale_draft")).toBe(true);
    expect(isStaleAIConflict("stale_tool_call")).toBe(true);
    expect(isStaleAIConflict("active_run")).toBe(true);
    expect(isStaleAIConflict("daily_run_limit")).toBe(false);
  });

  it("projects only safe run fields and never reasoning or provider payloads", () => {
    const run = {
      id: "run-1",
      status: "completed",
      model: "configured-model",
      prompt_template_version: "ai-runtime-v1",
      prompt_hash: "abc",
      input_tokens: 10,
      output_tokens: 4,
      cached_tokens: 2,
      latency_ms: 50,
      tool_calls: [],
      error_category: "",
      error_code: "",
    } as unknown as AIRun;
    const safe = safeRunTrace(run);
    expect(safe).toMatchObject({ id: "run-1", tokenUsage: { input: 10 } });
    expect(safe).not.toHaveProperty("reasoning");
    expect(safe).not.toHaveProperty("providerPayload");
  });
});
