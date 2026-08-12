import type { Conversation, GmailConnection } from "@workspace/api-client";

export type GmailComposerState =
  | "enabled"
  | "reauthorization_required"
  | "connection_degraded"
  | "thread_context_missing"
  | "provider_unavailable";

export function gmailComposerState(
  conversation: Conversation,
): GmailComposerState | null {
  if (conversation.channel_type !== "gmail") return null;
  return conversation.provider_context.state === "can_reply"
    ? "enabled"
    : ((conversation.provider_context.state ??
        "provider_unavailable") as GmailComposerState);
}

export function gmailNeedsAttention(connection: GmailConnection) {
  return (
    connection.connection_status !== "connected" ||
    !connection.health.watch_active ||
    !connection.has_encrypted_refresh_token ||
    !connection.health.scope_valid
  );
}
