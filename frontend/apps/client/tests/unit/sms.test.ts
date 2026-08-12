import type { Conversation, SMSConnection } from "@workspace/api-client";
import { describe, expect, it } from "vitest";
import {
  estimateSMSSegments,
  smsComposerState,
  smsNeedsAttention,
} from "../../src/lib/sms";

const conversation = (
  state: Conversation["provider_context"]["state"],
): Conversation =>
  ({ channel_type: "sms", provider_context: { state } }) as Conversation;

describe("SMS client policy", () => {
  it("estimates GSM-7 extensions and UCS-2 without altering content", () => {
    expect(estimateSMSSegments("Hello")).toMatchObject({
      encoding: "GSM-7",
      segments: 1,
    });
    expect(estimateSMSSegments("^".repeat(81))).toMatchObject({
      encoding: "GSM-7",
      units: 162,
      segments: 2,
    });
    expect(estimateSMSSegments("Здравствуйте 👋")).toMatchObject({
      encoding: "UCS-2",
      segments: 1,
    });
    expect(estimateSMSSegments("😀".repeat(36))).toMatchObject({
      encoding: "UCS-2",
      units: 72,
      segments: 2,
    });
  });

  it("keeps consent and opt-out composer states backend-authoritative", () => {
    expect(smsComposerState(conversation("can_reply"))).toBe("enabled");
    expect(smsComposerState(conversation("consent_required"))).toBe(
      "consent_required",
    );
    expect(smsComposerState(conversation("opted_out"))).toBe("opted_out");
  });

  it("flags degraded SMS connections", () => {
    expect(
      smsNeedsAttention({
        status: "connected",
        last_error_code: "",
      } as SMSConnection),
    ).toBe(false);
    expect(
      smsNeedsAttention({
        status: "degraded",
        last_error_code: "provider",
      } as SMSConnection),
    ).toBe(true);
  });
});
