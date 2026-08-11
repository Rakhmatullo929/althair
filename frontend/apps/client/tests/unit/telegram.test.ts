import { describe, expect, it } from "vitest";
import type {
  Conversation,
  TelegramBotConnection,
} from "@workspace/api-client";
import {
  telegramComposerState,
  telegramNeedsAttention,
  telegramStartParameter,
} from "@/lib/telegram";

function conversation(state: Conversation["provider_context"]["state"]) {
  return {
    channel_type: "telegram",
    provider_context: { state },
  } as Conversation;
}

describe("Telegram client policy helpers", () => {
  it("uses the backend provider policy for the composer", () => {
    expect(telegramComposerState(conversation("can_reply"))).toBe("enabled");
    expect(telegramComposerState(conversation("token_invalid"))).toBe(
      "token_invalid",
    );
    expect(telegramComposerState(conversation("user_not_started"))).toBe(
      "user_not_started",
    );
  });

  it("does not apply Telegram policy to another channel", () => {
    expect(
      telegramComposerState({
        ...conversation("can_reply"),
        channel_type: "instagram",
      }),
    ).toBeNull();
  });

  it("flags token, webhook, and connection health", () => {
    const healthy = {
      status: "connected",
      webhook_status: "verified",
      has_encrypted_token: true,
    } as TelegramBotConnection;
    expect(telegramNeedsAttention(healthy)).toBe(false);
    expect(
      telegramNeedsAttention({ ...healthy, webhook_status: "error" }),
    ).toBe(true);
    expect(
      telegramNeedsAttention({ ...healthy, has_encrypted_token: false }),
    ).toBe(true);
  });

  it("extracts only the Telegram start parameter", () => {
    expect(
      telegramStartParameter(
        "https://t.me/AlthairManagerBot?start=link_one_time_value",
      ),
    ).toBe("link_one_time_value");
    expect(telegramStartParameter("not a url")).toBe("");
  });
});
