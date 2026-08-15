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
const webhookToken = "test-only-twilio-webhook-token";

function twilioSignature(url: string, params: Record<string, string>) {
  const material = Object.keys(params)
    .sort()
    .reduce((value, key) => `${value}${key}${params[key]}`, url);
  return createHmac("sha1", webhookToken).update(material).digest("base64");
}

async function postSignedWebhook(
  request: APIRequestContext,
  externalUrl: string,
  params: Record<string, string>,
  signature = twilioSignature(externalUrl, params),
) {
  const parsed = new URL(externalUrl);
  return request.post(
    `http://localhost:8011${parsed.pathname}${parsed.search}`,
    {
      form: params,
      headers: { "X-Twilio-Signature": signature },
    },
  );
}

async function login(page: Page, email = "owner@portal.test") {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function tenant(request: APIRequestContext) {
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

async function connection(request: APIRequestContext, organizationId: string) {
  const response = await request.get(`${api}/integrations/sms/connections/`, {
    headers: { "X-Organization-ID": organizationId },
  });
  return ((await response.json()) as { results: Array<{ id: string }> })
    .results[0];
}

test.describe.serial("SMS messaging", () => {
  test("owner connects deterministic fake SMS without exposing secrets", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/sms");
    await expect(
      page.getByRole("heading", { name: "SMS messaging" }),
    ).toBeVisible();
    await expect(
      page.getByText("Signed, tenant-isolated and consent-aware"),
    ).toBeVisible();
    await page.getByLabel("Connection name").fill("Mehr Clinic SMS");
    await page.getByLabel("SMS-capable sender (E.164)").fill("+15550109999");
    await page.getByRole("button", { name: "Connect sender" }).click();
    await expect(
      page.getByRole("heading", { name: "+15550109999" }),
    ).toBeVisible();
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    const detail = await page.request.get(
      `${api}/integrations/sms/${sms.id}/`,
      {
        headers: context.headers,
      },
    );
    const text = await detail.text();
    expect(text).not.toContain("auth_token_encrypted");
    expect(text).not.toContain("api_key_secret_encrypted");
    expect(text).not.toContain("TWILIO_AUTH_TOKEN");
  });

  test("official-style signed inbound is destination-bound, rejects invalid signatures, and is idempotent", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    const detail = (await (
      await page.request.get(`${api}/integrations/sms/${sms.id}/`, {
        headers: context.headers,
      })
    ).json()) as {
      sender_address: string;
      webhook_urls: { inbound: string };
    };
    const valid = {
      AccountSid: "ACE2E1111111111111111111111111111",
      MessageSid: "SME2ESIGNED111111111111111111111111",
      From: "+14155559876",
      To: detail.sender_address,
      Body: "Signed inbound SMS",
      NumMedia: "0",
      FutureParameter: "validated-too",
    };
    const accepted = await postSignedWebhook(
      page.request,
      detail.webhook_urls.inbound,
      valid,
    );
    expect(accepted.status()).toBe(202);
    expect(((await accepted.json()) as { duplicate: boolean }).duplicate).toBe(
      false,
    );
    const duplicate = await postSignedWebhook(
      page.request,
      detail.webhook_urls.inbound,
      valid,
    );
    expect(duplicate.status()).toBe(202);
    expect(((await duplicate.json()) as { duplicate: boolean }).duplicate).toBe(
      true,
    );
    const rejected = await postSignedWebhook(
      page.request,
      detail.webhook_urls.inbound,
      { ...valid, MessageSid: "SME2EINVALID22222222222222222222222" },
      "invalid",
    );
    expect(rejected.status()).toBe(403);
    const conversations = (await (
      await page.request.get(`${api}/conversations/?channel_type=sms`, {
        headers: context.headers,
      })
    ).json()) as {
      results: Array<{ external_thread_id: string }>;
    };
    expect(
      conversations.results.filter(
        (row) => row.external_thread_id === "+14155559876",
      ),
    ).toHaveLength(1);
  });

  test("fake inbound creates a phone identity and manual SMS reply", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    const inbound = await page.request.post(
      `${api}/integrations/sms/${sms.id}/test/`,
      {
        headers: context.headers,
        data: { from: "+15550108888", body: "Здравствуйте 👋 Нужна помощь" },
      },
    );
    expect(inbound.status()).toBe(202);
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /\+15550108888/ })
      .first()
      .click();
    await expect(page.getByText("Здравствуйте 👋 Нужна помощь")).toBeVisible();
    await expect(page.getByText("SMS reply is available")).toBeVisible();
    await expect(
      page.getByText("SMS has no reliable read receipts"),
    ).toBeVisible();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("We can help by SMS.");
    await expect(page.getByText("Encoding:")).toBeVisible();
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page.getByLabel("Active conversation").getByText("We can help by SMS."),
    ).toBeVisible();
    await expect(
      page.getByLabel("Active conversation").getByText("Queued"),
    ).toBeVisible();
    await expect(
      page.getByLabel("Active conversation").getByText("Read", { exact: true }),
    ).toHaveCount(0);

    const conversations = (await (
      await page.request.get(`${api}/conversations/?channel_type=sms`, {
        headers: context.headers,
      })
    ).json()) as {
      results: Array<{ id: string; external_thread_id: string }>;
    };
    const conversationId = conversations.results.find(
      (row) => row.external_thread_id === "+15550108888",
    )!.id;
    const messages = (await (
      await page.request.get(
        `${api}/conversations/${conversationId}/messages/`,
        {
          headers: context.headers,
        },
      )
    ).json()) as {
      results: Array<{
        body: string;
        provider_message_id: string;
      }>;
    };
    const outbound = messages.results.find(
      (message) => message.body === "We can help by SMS.",
    )!;
    const detail = (await (
      await page.request.get(`${api}/integrations/sms/${sms.id}/`, {
        headers: context.headers,
      })
    ).json()) as {
      sender_address: string;
      webhook_urls: { status: string };
    };
    const delivered = await postSignedWebhook(
      page.request,
      detail.webhook_urls.status,
      {
        MessageSid: outbound.provider_message_id,
        MessageStatus: "delivered",
        From: detail.sender_address,
        To: "+15550108888",
        NumSegments: "1",
      },
    );
    expect(delivered.status()).toBe(202);
    const read = await postSignedWebhook(
      page.request,
      detail.webhook_urls.status,
      {
        MessageSid: outbound.provider_message_id,
        MessageStatus: "read",
        From: detail.sender_address,
        To: "+15550108888",
      },
    );
    expect(read.status()).toBe(202);
    await page.reload();
    await page
      .getByRole("button", { name: /\+15550108888/ })
      .first()
      .click();
    await expect(
      page.getByLabel("Active conversation").getByText("Delivered"),
    ).toBeVisible();
    await expect(
      page.getByLabel("Active conversation").getByText("Read", { exact: true }),
    ).toHaveCount(0);
  });

  test("STOP immediately blocks employees and START restores only the fake-confirmed recipient", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    await page.request.post(`${api}/integrations/sms/${sms.id}/test/`, {
      headers: context.headers,
      data: { from: "+15550108888", body: "STOP", opt_out_type: "STOP" },
    });
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /\+15550108888/ })
      .first()
      .click();
    await expect(
      page.getByText("Recipient opted out; outbound SMS is blocked"),
    ).toBeVisible();
    await expect(
      page.getByPlaceholder("Write a plain-text reply…"),
    ).toBeDisabled();
    await page.request.post(`${api}/integrations/sms/${sms.id}/test/`, {
      headers: context.headers,
      data: { from: "+15550108888", body: "START", opt_out_type: "START" },
    });
    await page.reload();
    await page
      .getByRole("button", { name: /\+15550108888/ })
      .first()
      .click();
    await expect(page.getByText("SMS reply is available")).toBeVisible();
    await expect(
      page.getByPlaceholder("Write a plain-text reply…"),
    ).toBeEnabled();
  });

  test("HELP is stored as compliance traffic without a business AI claim", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    await page.request.post(`${api}/integrations/sms/${sms.id}/test/`, {
      headers: context.headers,
      data: { from: "+15550107777", body: "HELP", opt_out_type: "HELP" },
    });
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /\+15550107777/ })
      .first()
      .click();
    await expect(page.getByText("HELP", { exact: true })).toBeVisible();
    await expect(page.getByText("Response draft")).toHaveCount(0);
  });

  test("settings expose exact callbacks, consent controls, health and fake testing", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    await page.goto(`/en/app/settings/channels/sms/${sms.id}`);
    await expect(
      page.getByRole("heading", { name: "Twilio webhook URLs" }),
    ).toBeVisible();
    await expect(page.getByLabel("Inbound message URL")).toHaveValue(
      /\/inbound\/$/,
    );
    await expect(page.getByLabel("Outbound status callback URL")).toHaveValue(
      /\/status\/$/,
    );
    await expect(
      page.getByRole("heading", { name: "STOP, START and HELP" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Run health check" }).click();
    await expect(page.getByText("SMS settings updated.")).toBeVisible();
    await page.getByRole("button", { name: "Receive test SMS" }).click();
    await expect(page.getByText("Fake inbound SMS accepted.")).toBeVisible();
  });

  test("RU, UZ and EN SMS settings are complete and mobile accessible", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const sms = await connection(page.request, context.id);
    for (const [locale, heading] of [
      ["ru", "SMS-сообщения"],
      ["uz", "SMS xabarlar"],
      ["en", "SMS messaging"],
    ] as const) {
      await page.goto(`/${locale}/app/settings/channels/sms`);
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`/en/app/settings/channels/sms/${sms.id}`);
    await expect(
      page.getByRole("heading", { name: "+15550109999" }),
    ).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
    const widths = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.client + 1);
  });

  test("other tenants and lower roles cannot inherit or configure the sender", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/sms");
    await expect(page.getByText("+15550109999")).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(page.getByText("+15550109999")).toHaveCount(0);
    await page.context().clearCookies();
    await login(page, "member@portal.test");
    await page.goto("/en/app/settings/channels/sms");
    await expect(
      page.getByRole("button", { name: "Connect sender" }),
    ).toHaveCount(0);
  });
});
