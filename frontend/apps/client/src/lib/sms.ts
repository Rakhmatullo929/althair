import type { Conversation, SMSConnection } from "@workspace/api-client";

const gsmBasic = new Set([
  ..."@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\u001bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà",
]);
const gsmExtended = new Set([..."^{}\\[~]|€"]);

export type SMSSegmentEstimate = {
  encoding: "GSM-7" | "UCS-2";
  units: number;
  segments: number;
  perSegment: number;
};

export function estimateSMSSegments(value: string): SMSSegmentEstimate {
  let units = 0;
  let gsm = true;
  for (const character of value) {
    if (gsmBasic.has(character)) units += 1;
    else if (gsmExtended.has(character)) units += 2;
    else {
      gsm = false;
      break;
    }
  }
  if (gsm) {
    const perSegment = units <= 160 ? 160 : 153;
    return {
      encoding: "GSM-7",
      units,
      segments: Math.max(1, Math.ceil(units / perSegment)),
      perSegment,
    };
  }
  units = [...value].reduce(
    (total, character) => total + (character.codePointAt(0)! > 0xffff ? 2 : 1),
    0,
  );
  const perSegment = units <= 70 ? 70 : 67;
  return {
    encoding: "UCS-2",
    units,
    segments: Math.max(1, Math.ceil(units / perSegment)),
    perSegment,
  };
}

export function smsComposerState(conversation: Conversation) {
  if (conversation.channel_type !== "sms") return null;
  return conversation.provider_context.state === "can_reply"
    ? ("enabled" as const)
    : (conversation.provider_context.state ?? "provider_unavailable");
}

export function smsNeedsAttention(connection: SMSConnection) {
  return (
    !["connected"].includes(connection.status) ||
    Boolean(connection.last_error_code)
  );
}
