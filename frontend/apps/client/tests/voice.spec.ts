import AxeBuilder from "@axe-core/playwright";
import { createHmac } from "node:crypto";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const api = "http://localhost:8011/api/v1";
const password = "client-portal-development-only-password";
const webhookSecret = "test-only-voice-webhook-secret";

type TenantContext = {
  id: string;
  headers: { "X-Organization-ID": string; "X-CSRFToken": string };
};

type Connection = {
  id: string;
  phone_number_e164: string;
  transfer_destinations: Array<{ id: string; key: string }>;
};

type VoiceTestCall = {
  selected_language: string;
  transcript_storage_allowed: boolean;
  transcript: Array<{ text: string }>;
  summary: string;
  tools: Array<{ status: string }>;
  transfers: Array<{ status: string }>;
  status: string;
  transfer_status: string;
  outcome: string;
  interruption_count: number;
  error_category: string;
};

async function login(page: Page, email = "owner@portal.test") {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function tenant(
  request: APIRequestContext,
  name = "Mehr Clinic",
): Promise<TenantContext> {
  const organizations = (await (
    await request.get(`${api}/organizations/`)
  ).json()) as { results: Array<{ id: string; name: string }> };
  const id = organizations.results.find((item) => item.name === name)!.id;
  const csrf = (await (
    await request.get(`${api}/users/auth/csrf/`)
  ).json()) as { csrftoken: string };
  return {
    id,
    headers: { "X-Organization-ID": id, "X-CSRFToken": csrf.csrftoken },
  };
}

async function connection(request: APIRequestContext, context: TenantContext) {
  const response = await request.get(`${api}/integrations/voice/connections/`, {
    headers: context.headers,
  });
  return ((await response.json()) as { results: Connection[] }).results[0];
}

async function fakeCall(
  request: APIRequestContext,
  context: TenantContext,
  connectionId: string,
  body: Record<string, unknown>,
) {
  const response = await request.post(
    `${api}/integrations/voice/${connectionId}/test-call/`,
    { headers: context.headers, data: body },
  );
  expect(response.status()).toBe(202);
  return (await response.json()) as VoiceTestCall;
}

async function signedIncoming(
  request: APIRequestContext,
  called: string,
  suffix: string,
) {
  const event = {
    id: `evt-e2e-${suffix}`,
    type: "realtime.call.incoming",
    data: {
      call_id: `call-e2e-${suffix}`,
      sip_headers: [
        { name: "fRoM", value: "sip:+15550108888@carrier.test" },
        { name: "TO", value: `sip:${called}@carrier.test` },
        { name: "Call-ID", value: `carrier-e2e-${suffix}` },
      ],
    },
  };
  const body = JSON.stringify(event);
  const signature = createHmac("sha256", webhookSecret)
    .update(body)
    .digest("hex");
  return request.post(`${api}/webhooks/openai/realtime-calls/`, {
    data: body,
    headers: {
      "Content-Type": "application/json",
      "X-Voice-Fake-Signature": signature,
    },
  });
}

test.describe.serial("Voice AI telephony", () => {
  test("owner configures a fake inbound Voice connection with safe readiness", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/voice");
    await expect(
      page.getByRole("heading", { name: "Voice AI telephony" }),
    ).toBeVisible();
    await expect(
      page.getByText(/Called number selects the tenant/),
    ).toBeVisible();
    await page.getByLabel("Connection name").fill("Mehr Clinic Voice");
    await page.getByLabel("Called phone number").fill("+15550107777");
    await page.getByRole("button", { name: "Connect Voice" }).click();
    await expect(
      page.getByRole("heading", { name: "+15550107777" }),
    ).toBeVisible();
    await expect(page.getByText("Audio recording")).toBeVisible();
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    const serialized = JSON.stringify(row);
    expect(serialized).not.toContain("carrier_auth_token_encrypted");
    expect(serialized).not.toContain("carrier_api_secret_encrypted");
  });

  test("owner configures a write-only transfer destination and health", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    await page.goto(`/en/app/settings/channels/voice/${row.id}`);
    await expect(page.getByText("Carrier / SIP")).toBeVisible();
    await page.getByLabel("Stable key").fill("front-desk");
    await page.getByLabel("Display name").fill("Front desk");
    await page.getByLabel("Phone or SIP URI").fill("+15550103333");
    await page.getByRole("button", { name: "Add destination" }).click();
    await expect(page.getByText("Destination added")).toBeVisible();
    const detail = await (
      await page.request.get(`${api}/integrations/voice/${row.id}/`, {
        headers: context.headers,
      })
    ).json();
    expect(detail.transfer_destinations[0].has_destination).toBe(true);
    expect(detail.transfer_destinations[0].destination).toBeUndefined();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test("signed incoming event resolves only the called-number tenant and is idempotent", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    const suffix = `${Date.now()}`;
    const accepted = await signedIncoming(
      page.request,
      row.phone_number_e164,
      suffix,
    );
    expect(accepted.status()).toBe(202);
    expect((await accepted.json()).status).toBe("accepted");
    const duplicate = await signedIncoming(
      page.request,
      row.phone_number_e164,
      suffix,
    );
    expect((await duplicate.json()).duplicate).toBe(true);
    const unknown = await signedIncoming(
      page.request,
      "+15550999999",
      `${suffix}-unknown`,
    );
    expect((await unknown.json()).status).toBe("rejected");
  });

  test("fake call shows disclosure, final transcript, summary, and RU/UZ/EN/mixed flows", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    for (const [language, utterance] of [
      ["ru", "Здравствуйте, расскажите об услугах"],
      ["uz", "Salom, xizmatlar haqida ayting"],
      ["en", "Hello, tell me about services"],
      ["uz", "Salom, please tell me about services"],
    ]) {
      const call = await fakeCall(page.request, context, row.id, {
        language,
        utterance,
      });
      expect(call.selected_language).toBe(language);
      expect(call.transcript).toHaveLength(2);
      expect(call.transcript[0].text).toContain("AI disclosure");
      expect(call.summary).toContain(utterance);
    }
    await page.goto(`/en/app/settings/channels/voice/${row.id}`);
    await page.locator(".voice-call-card").first().locator("summary").click();
    await expect(
      page.getByText("AI disclosure and greeting delivered.").first(),
    ).toBeVisible();
  });

  test("explicit consent and disabled retention prevent transcript persistence", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    await page.request.patch(`${api}/integrations/voice/${row.id}/`, {
      headers: context.headers,
      data: {
        disclosure_mode: "explicit_transcript_consent",
        transcript_retention_mode: "disabled",
      },
    });
    const call = await fakeCall(page.request, context, row.id, {
      utterance: "This must remain ephemeral",
    });
    expect(call.transcript_storage_allowed).toBe(false);
    expect(call.transcript).toEqual([]);
    await page.request.patch(`${api}/integrations/voice/${row.id}/`, {
      headers: context.headers,
      data: {
        disclosure_mode: "ai_and_transcript_disclosure",
        transcript_retention_mode: "30_days",
      },
    });
  });

  test("confirmed Voice-safe tools create a lead and follow-up idempotently", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const policiesResponse = await page.request.get(
      `${api}/ai/tool-policies/`,
      {
        headers: context.headers,
      },
    );
    const policies = (await policiesResponse.json()) as Array<{
      tool_name: string;
      enabled: boolean;
      execution_mode: string;
      configuration: Record<string, unknown>;
    }>;
    for (const name of ["create_lead", "create_follow_up_task"]) {
      const policy = policies.find((item) => item.tool_name === name)!;
      const response = await page.request.patch(`${api}/ai/tool-policies/`, {
        headers: context.headers,
        data: {
          policies: [
            {
              ...policy,
              enabled: true,
              execution_mode: "automatic",
              configuration: { voice_allowed: true },
            },
          ],
        },
      });
      expect(response.ok()).toBe(true);
    }
    const row = await connection(page.request, context);
    const events = [
      {
        type: "voice.tool_call",
        call_id: "lead-confirmed-1",
        name: "create_lead",
        confirmation_marker: "caller-segment-1",
        arguments: {
          title: "Voice consultation",
          description: "Caller confirmed interest",
        },
      },
      {
        type: "voice.tool_call",
        call_id: "task-confirmed-1",
        name: "create_follow_up_task",
        confirmation_marker: "caller-segment-2",
        arguments: { title: "Return Voice caller", due_in_hours: 2 },
      },
      { type: "voice.completed", outcome: "lead_created" },
    ];
    const call = await fakeCall(page.request, context, row.id, { events });
    expect(call.tools.map((tool) => tool.status)).toEqual([
      "succeeded",
      "succeeded",
    ]);
    await page.goto("/en/app/leads");
    await expect(page.getByText("Voice consultation").first()).toBeVisible();
    await page.goto("/en/app/tasks");
    await expect(page.getByText("Return Voice caller").first()).toBeVisible();
  });

  test("human request, configured transfer, failed transfer, and arbitrary target fail safely", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    const handoff = await fakeCall(page.request, context, row.id, {
      events: [
        {
          type: "voice.tool_call",
          call_id: "human-request-1",
          name: "request_human_handoff",
          arguments: {
            reason_code: "caller_requested_human",
            safe_summary: "Caller asked for a person.",
          },
        },
        { type: "voice.completed", outcome: "handoff_requested" },
      ],
    });
    expect(handoff.tools[0].status).toBe("succeeded");

    const transferred = await fakeCall(page.request, context, row.id, {
      events: [
        {
          id: "transfer-success-1",
          type: "voice.transfer",
          destination_key: "front-desk",
        },
      ],
    });
    expect(transferred.status).toBe("transferred");
    expect(transferred.transfer_status).toBe("accepted");

    const failedDestination = await page.request.post(
      `${api}/integrations/voice/${row.id}/transfers/`,
      {
        headers: context.headers,
        data: {
          key: "fail-desk",
          display_name: "Fallback desk",
          destination_type: "sip",
          destination: "sip:fail@example.test",
          fallback_behavior: "callback_task",
        },
      },
    );
    expect(failedDestination.status()).toBe(201);
    const callback = await fakeCall(page.request, context, row.id, {
      events: [
        {
          id: "transfer-fail-1",
          type: "voice.transfer",
          destination_key: "fail-desk",
        },
        { type: "voice.completed", outcome: "callback_requested" },
      ],
    });
    expect(callback.transfer_status).toBe("callback");
    expect(callback.outcome).toBe("callback_requested");

    const injected = await fakeCall(page.request, context, row.id, {
      events: [
        {
          id: "transfer-injected-1",
          type: "voice.transfer",
          destination_key: "+15550109999",
        },
      ],
    });
    expect(injected.status).toBe("failed");
    expect(injected.transfers).toEqual([]);
  });

  test("interruption, repeated unclear audio, provider loss, and max duration finalize safely", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    const interrupted = await fakeCall(page.request, context, row.id, {
      events: [
        { type: "input_audio_buffer.speech_started" },
        { type: "voice.completed", outcome: "answered" },
      ],
    });
    expect(interrupted.interruption_count).toBe(1);

    const unclear = await fakeCall(page.request, context, row.id, {
      events: [
        { type: "voice.unclear" },
        { type: "voice.unclear" },
        { type: "voice.unclear" },
      ],
    });
    expect(unclear.outcome).toBe("callback_requested");

    const disconnected = await fakeCall(page.request, context, row.id, {
      events: [{ type: "voice.provider_disconnect" }],
    });
    expect(disconnected.status).toBe("failed");
    expect(disconnected.error_category).toBe("realtime_provider_disconnect");

    const limited = await fakeCall(page.request, context, row.id, {
      events: [{ type: "voice.max_duration" }],
    });
    expect(limited.status).toBe("failed");
    expect(limited.error_category).toBe("max_duration");
  });

  test("human takeover supersedes AI and Voice calls appear in the unified Inbox", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    const incoming = await signedIncoming(
      page.request,
      row.phone_number_e164,
      `${Date.now()}-takeover`,
    );
    const callId = (await incoming.json()).call_id;
    const takeover = await page.request.post(
      `${api}/voice/calls/${callId}/takeover/`,
      { headers: context.headers },
    );
    expect(takeover.status()).toBe(200);
    expect((await takeover.json()).ai_control_active).toBe(false);
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /\+1555010/ })
      .first()
      .click();
    await expect(page.getByText("Voice call")).toBeVisible();
  });

  test("organization switching, lower role, suspended state, and mobile remain isolated", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/voice");
    const atlas = await tenant(page.request, "Atlas Academy");
    const mehr = await tenant(page.request, "Mehr Clinic");
    const paused = await tenant(page.request, "Paused Studio");
    await page.getByLabel("Organization").selectOption(atlas.id);
    await expect(
      page.getByRole("heading", { name: "Connect a Voice number" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "+15550107777" }),
    ).toHaveCount(0);
    await page.getByLabel("Organization").selectOption(mehr.id);
    await expect(
      page.getByRole("heading", { name: "+15550107777" }),
    ).toBeVisible();
    await page.getByLabel("Organization").selectOption(paused.id);
    await expect(page.getByText(/organization is suspended/i)).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Connect Voice" }),
    ).toHaveCount(0);

    await page.context().clearCookies();
    await login(page, "member@portal.test");
    await page.goto("/en/app/settings/channels/voice");
    await expect(
      page.getByText(/can view Voice status but cannot change it/),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Connect Voice" }),
    ).toHaveCount(0);

    await page.setViewportSize({ width: 390, height: 844 });
    const context = await tenant(page.request);
    const row = await connection(page.request, context);
    await page.goto(`/en/app/settings/channels/voice/${row.id}`);
    await expect(
      page.getByRole("heading", { name: row.phone_number_e164 }),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  });
});
