import type { Conversation, InstagramConnection } from "@workspace/api-client";

export type InstagramComposerState =
  | "enabled"
  | "waiting_for_customer"
  | "window_expired"
  | "human_agent_available"
  | "connection_expired"
  | "permission_missing"
  | "provider_degraded"
  | "provider_unavailable"
  | "organization_read_only";

export function instagramComposerState(
  conversation: Conversation,
): InstagramComposerState | null {
  if (conversation.channel_type !== "instagram") return null;
  const state = conversation.provider_context.state;
  if (state === "can_reply") return "enabled";
  return state ?? "provider_unavailable";
}

export function instagramNeedsAttention(connection: InstagramConnection) {
  return (
    connection.connection_status !== "connected" ||
    !connection.health.permissions_ok ||
    connection.health.token_expired ||
    connection.health.webhook_subscription !== "verified"
  );
}

export function remainingWindowMinutes(
  expiresAt: string | undefined,
  now = Date.now(),
) {
  if (!expiresAt) return null;
  return Math.max(0, Math.ceil((new Date(expiresAt).getTime() - now) / 60_000));
}
