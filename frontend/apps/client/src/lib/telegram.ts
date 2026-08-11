import type {
  Conversation,
  TelegramBotConnection,
} from "@workspace/api-client";

export type TelegramComposerState =
  | "enabled"
  | "token_invalid"
  | "webhook_degraded"
  | "connection_paused"
  | "bot_blocked"
  | "user_not_started"
  | "provider_unavailable";

export function telegramComposerState(
  conversation: Conversation,
): TelegramComposerState | null {
  if (conversation.channel_type !== "telegram") return null;
  const state = conversation.provider_context.state;
  return state === "can_reply"
    ? "enabled"
    : ((state ?? "provider_unavailable") as TelegramComposerState);
}

export function telegramNeedsAttention(connection: TelegramBotConnection) {
  return (
    connection.status !== "connected" ||
    connection.webhook_status !== "verified" ||
    !connection.has_encrypted_token
  );
}

export function telegramStartParameter(url: string) {
  try {
    return new URL(url).searchParams.get("start") ?? "";
  } catch {
    return "";
  }
}
