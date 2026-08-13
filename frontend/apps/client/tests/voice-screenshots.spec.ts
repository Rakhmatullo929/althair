import { createHmac } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const api = "http://localhost:8011/api/v1";
const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/voice",
);
const screenshotStyle = "nextjs-portal { display: none !important; }";

async function login(page: Page) {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill("owner@portal.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("client-portal-development-only-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function context(request: APIRequestContext) {
  const organizations = (await (
    await request.get(`${api}/organizations/`)
  ).json()) as { results: Array<{ id: string; name: string }> };
  const id = organizations.results.find(
    (item) => item.name === "Mehr Clinic",
  )!.id;
  const csrf = (await (
    await request.get(`${api}/users/auth/csrf/`)
  ).json()) as { csrftoken: string };
  return {
    id,
    headers: { "X-Organization-ID": id, "X-CSRFToken": csrf.csrftoken },
  };
}

async function fakeCall(
  request: APIRequestContext,
  headers: Record<string, string>,
  connectionId: string,
  body: Record<string, unknown>,
) {
  const response = await request.post(
    `${api}/integrations/voice/${connectionId}/test-call/`,
    { headers, data: body },
  );
  expect(response.status()).toBe(202);
  return response.json();
}

async function activeCall(request: APIRequestContext, called: string) {
  const suffix = Date.now().toString();
  const event = {
    id: `screenshot-event-${suffix}`,
    type: "realtime.call.incoming",
    data: {
      call_id: `screenshot-active-${suffix}`,
      sip_headers: [
        { name: "From", value: "sip:+15550105555@fake.invalid" },
        { name: "To", value: `sip:${called}@fake.invalid` },
        { name: "Call-ID", value: `carrier-${suffix}` },
      ],
    },
  };
  const body = JSON.stringify(event);
  return request.post(`${api}/webhooks/openai/realtime-calls/`, {
    data: body,
    headers: {
      "Content-Type": "application/json",
      "X-Voice-Fake-Signature": createHmac(
        "sha256",
        "test-only-voice-webhook-secret",
      )
        .update(body)
        .digest("hex"),
    },
  });
}

async function screenshot(page: Page, filename: string, fullPage = true) {
  await page.screenshot({
    path: resolve(screenshotDir, filename),
    fullPage,
    style: screenshotStyle,
  });
}

async function openNewestCall(page: Page, connectionId: string) {
  await page.goto(`/en/app/settings/channels/voice/${connectionId}`);
  const newest = page.locator(".voice-call-card").first();
  await expect(newest).toBeVisible();
  await newest.locator("summary").click();
  return newest;
}

test("@screenshots Voice AI evidence", async ({ page }) => {
  test.setTimeout(180_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  const tenant = await context(page.request);

  await page.goto("/en/app/settings/channels/voice");
  await expect(
    page.getByRole("heading", { name: "Connect a Voice number" }),
  ).toBeVisible();
  await screenshot(page, "01-voice-setup.png");

  const created = await page.request.post(
    `${api}/integrations/voice/connections/`,
    {
      headers: tenant.headers,
      data: {
        carrier: "fake",
        ownership_mode: "platform_managed",
        display_name: "Mehr Clinic Voice",
        phone_number_e164: "+15550107777",
        ai_mode: "autopilot",
        disclosure_mode: "ai_and_transcript_disclosure",
        transcript_retention_mode: "30_days",
      },
    },
  );
  expect(created.status()).toBe(201);
  const voice = (await created.json()) as {
    id: string;
    phone_number_e164: string;
  };

  await page.goto(`/en/app/settings/channels/voice/${voice.id}`);
  await expect(page.getByText("Carrier / SIP")).toBeVisible();
  await screenshot(page, "02-provider-sip-worker-health.png");

  for (const target of [
    {
      key: "front-desk",
      display_name: "Front desk",
      destination_type: "phone",
      destination: "+15550103333",
      fallback_behavior: "callback_task",
    },
    {
      key: "fail-desk",
      display_name: "Callback fallback",
      destination_type: "sip",
      destination: "sip:fail@example.test",
      fallback_behavior: "callback_task",
    },
  ]) {
    expect(
      (
        await page.request.post(
          `${api}/integrations/voice/${voice.id}/transfers/`,
          { headers: tenant.headers, data: target },
        )
      ).status(),
    ).toBe(201);
  }
  await page.reload();
  await expect(page.getByText("Front desk")).toBeVisible();
  await screenshot(page, "03-transfer-destinations.png");
  await screenshot(page, "04-disclosure-transcript-retention.png");

  const active = await activeCall(page.request, voice.phone_number_e164);
  expect(active.status()).toBe(202);
  await openNewestCall(page, voice.id);
  await screenshot(page, "05-incoming-active-call.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    language: "ru",
    utterance: "Здравствуйте, соедините с администратором",
  });
  let newest = await openNewestCall(page, voice.id);
  await expect(
    newest.getByText("Здравствуйте, соедините с администратором", {
      exact: true,
    }),
  ).toBeVisible();
  await screenshot(page, "06-completed-summary-transcript-ru.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    events: [
      {
        type: "voice.tool_call",
        call_id: "screenshot-handoff",
        name: "request_human_handoff",
        arguments: {
          reason_code: "caller_requested_human",
          safe_summary: "Caller requested a person.",
        },
      },
      { type: "voice.completed", outcome: "handoff_requested" },
    ],
  });
  newest = await openNewestCall(page, voice.id);
  await expect(newest.getByText(/request_human_handoff/)).toBeVisible();
  await screenshot(page, "07-human-handoff.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    events: [
      {
        id: "screenshot-transfer",
        type: "voice.transfer",
        destination_key: "front-desk",
      },
    ],
  });
  newest = await openNewestCall(page, voice.id);
  await expect(newest.getByText("accepted", { exact: true })).toBeVisible();
  await screenshot(page, "08-transfer-success.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    events: [
      {
        id: "screenshot-callback",
        type: "voice.transfer",
        destination_key: "fail-desk",
      },
      { type: "voice.completed", outcome: "callback_requested" },
    ],
  });
  newest = await openNewestCall(page, voice.id);
  await expect(newest.getByText("callback", { exact: true })).toBeVisible();
  await screenshot(page, "09-callback-fallback.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    events: [{ type: "voice.provider_disconnect" }],
  });
  newest = await openNewestCall(page, voice.id);
  await expect(newest.locator(".status-failed")).toBeVisible();
  await screenshot(page, "10-provider-failure.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    events: [{ type: "voice.max_duration" }],
  });
  newest = await openNewestCall(page, voice.id);
  await expect(newest.locator(".status-failed")).toBeVisible();
  await screenshot(page, "11-maximum-duration-limit.png");

  await fakeCall(page.request, tenant.headers, voice.id, {
    language: "uz",
    utterance: "Salom, xizmatlaringiz haqida ayting",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  newest = await openNewestCall(page, voice.id);
  await expect(newest.getByText("uz", { exact: true })).toBeVisible();
  await screenshot(page, "12-uz-mobile-call-detail.png", false);

  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /\+1555010/ })
    .first()
    .click();
  await expect(page.getByText("Voice call")).toBeVisible();
  await screenshot(page, "13-unified-inbox-mobile.png", false);
});
