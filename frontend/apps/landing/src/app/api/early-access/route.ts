import { NextResponse, type NextRequest } from "next/server";
import {
  earlyAccessSchema,
  type EarlyAccessResponse,
} from "@/lib/early-access";
import { serverEnv } from "@/lib/env";

export const runtime = "nodejs";

const WINDOW_MS = 10 * 60 * 1000;
const MAX_REQUESTS = 5;
const attempts = new Map<string, number[]>();

function isRateLimited(key: string, now: number) {
  const recent = (attempts.get(key) ?? []).filter(
    (time) => now - time < WINDOW_MS,
  );
  recent.push(now);
  attempts.set(key, recent);
  return recent.length > MAX_REQUESTS;
}

export async function POST(request: NextRequest) {
  const now = Date.now();
  const ip =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";

  if (isRateLimited(ip, now)) {
    return NextResponse.json<EarlyAccessResponse>(
      { ok: false, code: "RATE_LIMITED" },
      { status: 429 },
    );
  }

  let input: unknown;
  try {
    input = await request.json();
  } catch {
    return NextResponse.json<EarlyAccessResponse>(
      { ok: false, code: "INVALID" },
      { status: 400 },
    );
  }

  const parsed = earlyAccessSchema.safeParse(input);
  if (!parsed.success) {
    return NextResponse.json<EarlyAccessResponse>(
      {
        ok: false,
        code: "INVALID",
        fieldErrors: parsed.error.flatten().fieldErrors,
      },
      { status: 400 },
    );
  }

  if (parsed.data.website || now - parsed.data.startedAt < 1200) {
    return NextResponse.json<EarlyAccessResponse>(
      { ok: false, code: "INVALID" },
      { status: 400 },
    );
  }

  const { website: _honeypot, startedAt: _startedAt, ...lead } = parsed.data;
  void _honeypot;
  void _startedAt;

  const webhookUrl = serverEnv.LEAD_WEBHOOK_URL;
  if (!webhookUrl) {
    if (process.env.NODE_ENV === "development") {
      console.info("[early-access demo]", {
        companyName: lead.companyName,
        industry: lead.industry,
        preferredChannel: lead.preferredChannel,
        locale: lead.locale,
        contactType: lead.contact.includes("@") ? "email" : "phone",
      });
    }

    return NextResponse.json<EarlyAccessResponse>(
      { ok: false, code: "DEMO_MODE" },
      { status: 503 },
    );
  }

  try {
    const secret = serverEnv.LEAD_WEBHOOK_SECRET;
    const response = await fetch(webhookUrl, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(secret ? { "x-lead-webhook-secret": secret } : {}),
      },
      body: JSON.stringify({
        ...lead,
        receivedAt: new Date(now).toISOString(),
      }),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });

    if (!response.ok) throw new Error(`Webhook returned ${response.status}`);

    return NextResponse.json<EarlyAccessResponse>({
      ok: true,
      code: "DELIVERED",
    });
  } catch (error) {
    console.error("Early-access delivery failed", {
      message:
        error instanceof Error ? error.message : "Unknown delivery error",
    });
    return NextResponse.json<EarlyAccessResponse>(
      { ok: false, code: "DELIVERY_FAILED" },
      { status: 502 },
    );
  }
}
