import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { createHmac } from "node:crypto";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const api = "http://localhost:8011/api/v1";
const screenshotDir = resolve(process.cwd(), "../../artifacts/screenshots/sms");
const screenshotStyle = "nextjs-portal { display: none !important; }";
const webhookToken = "test-only-twilio-webhook-token";

function signature(url: string, params: Record<string, string>) {
  const material = Object.keys(params)
    .sort()
    .reduce((value, key) => `${value}${key}${params[key]}`, url);
  return createHmac("sha1", webhookToken).update(material).digest("base64");
}

async function callback(
  request: APIRequestContext,
  url: string,
  params: Record<string, string>,
) {
  const parsed = new URL(url);
  return request.post(`http://localhost:8011${parsed.pathname}`, {
    form: params,
    headers: { "X-Twilio-Signature": signature(url, params) },
  });
}

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

async function tenant(request: APIRequestContext) {
  const organizations = (await (
    await request.get(`${api}/organizations/`)
  ).json()) as {
    results: Array<{ id: string; name: string }>;
  };
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

test("@screenshots SMS evidence", async ({ page }) => {
  test.setTimeout(120_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  const context = await tenant(page.request);
  let list = (await (
    await page.request.get(`${api}/integrations/sms/connections/`, {
      headers: context.headers,
    })
  ).json()) as { results: Array<{ id: string }> };
  if (!list.results.length) {
    const created = await page.request.post(
      `${api}/integrations/sms/connections/`,
      {
        headers: context.headers,
        data: {
          provider: "fake",
          ownership_mode: "platform_managed",
          display_name: "Mehr Clinic SMS",
          sender_address: "+15550109999",
        },
      },
    );
    expect(created.status()).toBe(201);
    list = { results: [{ id: ((await created.json()) as { id: string }).id }] };
  }
  const connectionId = list.results[0].id;
  const detail = (await (
    await page.request.get(`${api}/integrations/sms/${connectionId}/`, {
      headers: context.headers,
    })
  ).json()) as {
    channel_connection: string;
    sender_address: string;
    webhook_urls: { status: string };
  };
  await page.goto("/en/app/settings/channels/sms");
  await expect(
    page.getByRole("heading", { name: "SMS messaging" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "01-sms-connections.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto(`/en/app/settings/channels/sms/${connectionId}`);
  await expect(
    page.getByRole("heading", { name: detail.sender_address }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "02-sms-health-consent.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.post(`${api}/integrations/sms/${connectionId}/test/`, {
    headers: context.headers,
    data: { from: "+15550106666", body: "Здравствуйте 👋 SMS screenshot" },
  });
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /\+15550106666/ })
    .first()
    .click();
  await page.screenshot({
    path: resolve(screenshotDir, "03-sms-inbox.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page
    .getByPlaceholder("Write a plain-text reply…")
    .fill("😀".repeat(140));
  await expect(page.getByText("Confirm this multi-segment SMS")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "04-sms-unicode-segment-warning.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page
    .getByPlaceholder("Write a plain-text reply…")
    .fill("Manual SMS screenshot reply");
  await page.getByRole("button", { name: "Send reply" }).click();
  await expect(
    page
      .getByLabel("Active conversation")
      .getByText("Manual SMS screenshot reply"),
  ).toBeVisible();
  const conversations = (await (
    await page.request.get(`${api}/conversations/?channel_type=sms`, {
      headers: context.headers,
    })
  ).json()) as {
    results: Array<{ id: string; external_thread_id: string }>;
  };
  const conversationId = conversations.results.find(
    (row) => row.external_thread_id === "+15550106666",
  )!.id;
  let messages = (await (
    await page.request.get(`${api}/conversations/${conversationId}/messages/`, {
      headers: context.headers,
    })
  ).json()) as {
    results: Array<{
      body: string;
      provider_message_id: string;
    }>;
  };
  const deliveredMessage = messages.results.find(
    (row) => row.body === "Manual SMS screenshot reply",
  )!;
  await callback(page.request, detail.webhook_urls.status, {
    MessageSid: deliveredMessage.provider_message_id,
    MessageStatus: "delivered",
    From: detail.sender_address,
    To: "+15550106666",
    NumSegments: "1",
  });
  await page.reload();
  await page
    .getByRole("button", { name: /\+15550106666/ })
    .first()
    .click();
  await expect(page.getByText("Delivered")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "05-sms-delivered-state.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  const failed = await page.request.post(
    `${api}/conversations/${conversationId}/messages/`,
    {
      headers: context.headers,
      data: {
        body: "Failed delivery screenshot",
        client_message_id: "sms-screenshot-failed",
      },
    },
  );
  expect(failed.status()).toBe(201);
  messages = (await (
    await page.request.get(`${api}/conversations/${conversationId}/messages/`, {
      headers: context.headers,
    })
  ).json()) as typeof messages;
  const failedMessage = messages.results.find(
    (row) => row.body === "Failed delivery screenshot",
  )!;
  await callback(page.request, detail.webhook_urls.status, {
    MessageSid: failedMessage.provider_message_id,
    MessageStatus: "failed",
    ErrorCode: "30006",
    From: detail.sender_address,
    To: "+15550106666",
  });
  await page.reload();
  await page
    .getByRole("button", { name: /\+15550106666/ })
    .first()
    .click();
  await expect(page.getByText("Failed", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "06-sms-failed-state.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.post(`${api}/integrations/sms/${connectionId}/test/`, {
    headers: context.headers,
    data: { from: "+15550106666", body: "STOP", opt_out_type: "STOP" },
  });
  await page.reload();
  await page
    .getByRole("button", { name: /\+15550106666/ })
    .first()
    .click();
  await page.screenshot({
    path: resolve(screenshotDir, "07-sms-stop-opted-out.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.post(`${api}/integrations/sms/${connectionId}/test/`, {
    headers: context.headers,
    data: { from: "+15550106666", body: "START", opt_out_type: "START" },
  });
  await page.reload();
  await page
    .getByRole("button", { name: /\+15550106666/ })
    .first()
    .click();
  await expect(page.getByText("SMS reply is available")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "08-sms-start-restored.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  const runtime = (await (
    await page.request.get(`${api}/ai/runtime-config/`, {
      headers: context.headers,
    })
  ).json()) as { allowed_channel_connections: string[] };
  await page.request.patch(`${api}/ai/runtime-config/`, {
    headers: context.headers,
    data: {
      enabled: true,
      default_mode: "suggest",
      daily_run_limit: 100,
      allowed_channel_connections: Array.from(
        new Set([
          ...runtime.allowed_channel_connections,
          detail.channel_connection,
        ]),
      ),
    },
  });
  await page.request.patch(`${api}/integrations/sms/${connectionId}/`, {
    headers: context.headers,
    data: { ai_mode: "suggest" },
  });
  await page.request.post(`${api}/integrations/sms/${connectionId}/test/`, {
    headers: context.headers,
    data: {
      from: "+15550105555",
      body: "Hello, which services are available?",
    },
  });
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /\+15550105555/ })
    .first()
    .click();
  await expect(page.getByText("Response draft")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "09-sms-ai-draft.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.patch(`${api}/integrations/sms/${connectionId}/`, {
    headers: context.headers,
    data: { ai_mode: "manual" },
  });
  await page.request.post(`${api}/integrations/sms/${connectionId}/test/`, {
    headers: context.headers,
    data: { from: "+14155550000", body: "Provider failure example" },
  });
  const failureConversations = (await (
    await page.request.get(`${api}/conversations/?channel_type=sms`, {
      headers: context.headers,
    })
  ).json()) as typeof conversations;
  const failureConversationId = failureConversations.results.find(
    (row) => row.external_thread_id === "+14155550000",
  )!.id;
  for (let index = 1; index <= 3; index += 1) {
    const response = await page.request.post(
      `${api}/conversations/${failureConversationId}/messages/`,
      {
        headers: context.headers,
        data: {
          body: `Provider failure ${index}`,
          client_message_id: `sms-screenshot-provider-failure-${index}`,
        },
      },
    );
    expect(response.status()).toBe(201);
  }
  const degraded = (await (
    await page.request.get(`${api}/integrations/sms/${connectionId}/`, {
      headers: context.headers,
    })
  ).json()) as { status: string };
  expect(degraded.status).toBe("degraded");
  await page.goto(`/en/app/settings/channels/sms/${connectionId}`);
  await page.reload();
  await expect(page.getByText("degraded", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "10-sms-provider-degraded.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto(`/ru/app/settings/channels/sms/${connectionId}`);
  await page.screenshot({
    path: resolve(screenshotDir, "11-sms-ru.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto(`/uz/app/settings/channels/sms/${connectionId}`);
  await page.screenshot({
    path: resolve(screenshotDir, "12-sms-uz.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/en/app/settings/channels/sms/${connectionId}`);
  await page.screenshot({
    path: resolve(screenshotDir, "13-sms-mobile.png"),
    fullPage: true,
    style: screenshotStyle,
  });
});
