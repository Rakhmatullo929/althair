import type {
  AIRun,
  Conversation,
  OrganizationRole,
} from "@workspace/api-client";

export const aiQueryKeys = {
  root: (organizationId: string) => ["ai-runtime", organizationId] as const,
  config: (organizationId: string) =>
    [...aiQueryKeys.root(organizationId), "config"] as const,
  policies: (organizationId: string) =>
    [...aiQueryKeys.root(organizationId), "policies"] as const,
  usage: (organizationId: string) =>
    [...aiQueryKeys.root(organizationId), "usage"] as const,
  runs: (organizationId: string) =>
    [...aiQueryKeys.root(organizationId), "runs"] as const,
  conversation: (organizationId: string, conversationId: string) =>
    [
      ...aiQueryKeys.root(organizationId),
      "conversation",
      conversationId,
    ] as const,
};

export function aiControlState(
  conversation: Pick<Conversation, "ai_state">,
  role: OrganizationRole,
  organizationStatus: string,
) {
  const readOnly = ["suspended", "archived"].includes(organizationStatus);
  const canOperate = role !== "viewer" && !readOnly;
  const canManage = ["owner", "admin", "manager"].includes(role) && !readOnly;
  return {
    canPause:
      canOperate &&
      ["suggest", "autopilot_test"].includes(conversation.ai_state),
    canResume:
      canManage &&
      ["off", "paused_by_human", "handoff_required"].includes(
        conversation.ai_state,
      ),
    canGenerate: canOperate && conversation.ai_state === "suggest",
    canApproveTool: canManage,
    readOnly,
  };
}

export function safeRunTrace(run: AIRun) {
  return {
    id: run.id,
    status: run.status,
    model: run.model,
    promptTemplateVersion: run.prompt_template_version,
    promptHash: run.prompt_hash,
    tokenUsage: {
      input: run.input_tokens,
      output: run.output_tokens,
      cached: run.cached_tokens,
    },
    latencyMs: run.latency_ms,
    tools: run.tool_calls.map((item) => ({
      name: item.tool_name,
      status: item.status,
      output: item.output_redacted,
    })),
    error: run.error_code
      ? { category: run.error_category, code: run.error_code }
      : null,
  };
}

export function isStaleAIConflict(code: string) {
  return ["stale_draft", "stale_tool_call", "active_run"].includes(code);
}
