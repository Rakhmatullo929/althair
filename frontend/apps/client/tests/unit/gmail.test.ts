import { describe, expect, it } from "vitest";
import type { Conversation, GmailConnection } from "@workspace/api-client";
import { gmailComposerState, gmailNeedsAttention } from "@/lib/gmail";

function conversation(state: Conversation["provider_context"]["state"]) {
  return { channel_type: "gmail", provider_context: { state } } as Conversation;
}

describe("Gmail client policy helpers", () => {
  it("uses the backend provider policy for the composer", () => {
    expect(gmailComposerState(conversation("can_reply"))).toBe("enabled");
    expect(gmailComposerState(conversation("reauthorization_required"))).toBe(
      "reauthorization_required",
    );
    expect(gmailComposerState(conversation("thread_context_missing"))).toBe(
      "thread_context_missing",
    );
    expect(gmailComposerState(conversation("watch_expired"))).toBe(
      "watch_expired",
    );
    expect(gmailComposerState(conversation("automated_message"))).toBe(
      "automated_message",
    );
    expect(gmailComposerState(conversation("encrypted_message"))).toBe(
      "encrypted_message",
    );
  });

  it("does not apply Gmail policy to another channel", () => {
    expect(
      gmailComposerState({
        ...conversation("can_reply"),
        channel_type: "telegram",
      }),
    ).toBeNull();
  });

  it("flags token, watch, scope and connection health", () => {
    const healthy = {
      connection_status: "connected",
      has_encrypted_refresh_token: true,
      health: { watch_active: true, scope_valid: true },
    } as GmailConnection;
    expect(gmailNeedsAttention(healthy)).toBe(false);
    expect(
      gmailNeedsAttention({ ...healthy, connection_status: "degraded" }),
    ).toBe(true);
    expect(
      gmailNeedsAttention({ ...healthy, has_encrypted_refresh_token: false }),
    ).toBe(true);
    expect(
      gmailNeedsAttention({
        ...healthy,
        health: { ...healthy.health, watch_active: false },
      }),
    ).toBe(true);
    expect(
      gmailNeedsAttention({
        ...healthy,
        health: { ...healthy.health, scope_valid: false },
      }),
    ).toBe(true);
  });
});
