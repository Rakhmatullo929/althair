import type { Conversation, InstagramConnection } from "@workspace/api-client";
import { describe, expect, it } from "vitest";
import {
  instagramComposerState,
  instagramNeedsAttention,
  remainingWindowMinutes,
} from "@/lib/instagram";

const conversation = (
  state: Conversation["provider_context"]["state"],
): Conversation =>
  ({
    channel_type: "instagram",
    provider_context: { state },
  }) as Conversation;

const connection = (overrides: Partial<InstagramConnection> = {}) =>
  ({
    connection_status: "connected",
    health: {
      permissions_ok: true,
      token_expired: false,
      webhook_subscription: "verified",
    },
    ...overrides,
  }) as InstagramConnection;

describe("Instagram UI policy", () => {
  it.each([
    ["can_reply", "enabled"],
    ["waiting_for_customer", "waiting_for_customer"],
    ["window_expired", "window_expired"],
    ["human_agent_available", "human_agent_available"],
    ["connection_expired", "connection_expired"],
    ["permission_missing", "permission_missing"],
    ["provider_degraded", "provider_degraded"],
  ] as const)("maps authoritative %s state", (state, expected) => {
    expect(instagramComposerState(conversation(state))).toBe(expected);
  });

  it("does not apply Instagram policy to another provider", () => {
    expect(
      instagramComposerState({
        ...conversation("window_expired"),
        channel_type: "webchat",
      }),
    ).toBeNull();
  });

  it("flags token, permission, subscription and connection failures", () => {
    expect(instagramNeedsAttention(connection())).toBe(false);
    expect(
      instagramNeedsAttention(
        connection({ health: { ...connection().health, token_expired: true } }),
      ),
    ).toBe(true);
    expect(
      instagramNeedsAttention(
        connection({
          health: { ...connection().health, permissions_ok: false },
        }),
      ),
    ).toBe(true);
    expect(
      instagramNeedsAttention(connection({ connection_status: "degraded" })),
    ).toBe(true);
  });

  it("calculates a non-negative window countdown", () => {
    const now = Date.parse("2026-08-10T10:00:00Z");
    expect(remainingWindowMinutes("2026-08-10T10:30:01Z", now)).toBe(31);
    expect(remainingWindowMinutes("2026-08-10T09:00:00Z", now)).toBe(0);
    expect(remainingWindowMinutes(undefined, now)).toBeNull();
  });
});
