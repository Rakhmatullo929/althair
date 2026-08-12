import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const api = "http://localhost:8011/api/v1";
const password = "client-portal-development-only-password";

async function login(page: Page, email = "owner@portal.test") {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function tenantContext(request: APIRequestContext) {
  const organizations = await request.get(`${api}/organizations/`);
  const payload = (await organizations.json()) as {
    results: Array<{ id: string; name: string }>;
  };
  const organization = payload.results.find(
    (item) => item.name === "Mehr Clinic",
  )!;
  const csrfResponse = await request.get(`${api}/users/auth/csrf/`);
  const csrf = (await csrfResponse.json()) as { csrftoken: string };
  return {
    id: organization.id,
    headers: {
      "X-Organization-ID": organization.id,
      "X-CSRFToken": csrf.csrftoken,
    },
  };
}

async function connection(request: APIRequestContext, organizationId: string) {
  const response = await request.get(`${api}/integrations/instagram/`, {
    headers: { "X-Organization-ID": organizationId },
  });
  const payload = (await response.json()) as {
    results: Array<{ id: string; username: string }>;
  };
  return payload.results[0];
}

test.describe.serial("Instagram Messaging", () => {
  test("owner completes fake Business Login and duplicate account is rejected", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/instagram");
    await page.getByRole("button", { name: "Connect Instagram" }).click();
    await expect(
      page.getByRole("heading", { name: "@althair_demo" }),
    ).toBeVisible();
    await expect(
      page.getByText("Encrypted server-side connection"),
    ).toBeVisible();
    const tenant = await tenantContext(page.request);
    const start = await page.request.get(
      `${api}/integrations/instagram/oauth/start/?redirect=/en/app/settings/channels/instagram`,
      { headers: { "X-Organization-ID": tenant.id } },
    );
    const state = ((await start.json()) as { state: string }).state;
    const duplicate = await page.request.get(
      `${api}/integrations/instagram/oauth/callback/?state=${encodeURIComponent(state)}&code=fake_connect:ig_professional_demo:duplicate:BUSINESS`,
    );
    expect(duplicate.status()).toBe(409);
    expect(await duplicate.text()).not.toContain("fake-access");
  });

  test("signed fake webhook creates one scoped Instagram contact and manual reply", async ({
    page,
  }) => {
    await login(page);
    const tenant = await tenantContext(page.request);
    const connected = await connection(page.request, tenant.id);
    const inboundTimestamp = Date.now();
    const event = await page.request.post(
      `${api}/integrations/instagram/${connected.id}/test-event/`,
      {
        headers: tenant.headers,
        data: {
          event_type: "story_reply",
          sender_id: "ig_e2e_customer",
          message_id: "ig_e2e_first",
          timestamp: inboundTimestamp,
          text: "Replying to your story",
        },
      },
    );
    expect(event.status()).toBe(202);
    const duplicate = await page.request.post(
      `${api}/integrations/instagram/${connected.id}/test-event/`,
      {
        headers: tenant.headers,
        data: {
          event_type: "story_reply",
          sender_id: "ig_e2e_customer",
          message_id: "ig_e2e_first",
          timestamp: inboundTimestamp,
          text: "Replying to your story",
        },
      },
    );
    expect((await duplicate.json()).duplicates).toBe(1);
    await page.goto("/en/app/inbox");
    await expect(page.getByText("Instagram user stomer").first()).toBeVisible();
    await page
      .getByRole("button", { name: /Instagram user stomer/ })
      .first()
      .click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("Replying to your story"),
    ).toBeVisible();
    await expect(
      page.getByText("Can reply inside the standard window"),
    ).toBeVisible();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("Manual reply from Unified Inbox");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("Manual reply from Unified Inbox"),
    ).toBeVisible();
    await page.goto("/en/app/contacts");
    await expect(page.getByText("Instagram").first()).toBeVisible();
  });

  test("invalid signature fails and reaction/read/echo reconcile", async ({
    page,
  }) => {
    const invalid = await page.request.post(`${api}/webhooks/instagram/`, {
      headers: { "X-Hub-Signature-256": "sha256=invalid" },
      data: { object: "instagram", entry: [] },
    });
    expect(invalid.status()).toBe(403);
    await login(page);
    const tenant = await tenantContext(page.request);
    const connected = await connection(page.request, tenant.id);
    for (const event_type of ["reaction", "read", "echo"]) {
      const response = await page.request.post(
        `${api}/integrations/instagram/${connected.id}/test-event/`,
        {
          headers: tenant.headers,
          data: {
            event_type,
            sender_id: "ig_e2e_customer",
            message_id: `ig_e2e_${event_type}`,
            text: "Provider reconciliation",
          },
        },
      );
      expect(response.status()).toBe(202);
    }
  });

  test("expired window blocks normal and AI send while Human Agent is manual-only", async ({
    page,
  }) => {
    await login(page);
    const tenant = await tenantContext(page.request);
    const connected = await connection(page.request, tenant.id);
    const rows = await page.request.get(
      `${api}/conversations/?channel_type=instagram`,
      { headers: { "X-Organization-ID": tenant.id } },
    );
    const conversationId = (
      (await rows.json()) as { results: Array<{ id: string }> }
    ).results[0].id;
    await page.request.post(
      `${api}/integrations/instagram/${connected.id}/test-control/`,
      { headers: tenant.headers, data: { action: "approve_human_agent" } },
    );
    await page.request.post(
      `${api}/integrations/instagram/${connected.id}/test-control/`,
      {
        headers: tenant.headers,
        data: { action: "expire_window", conversation_id: conversationId },
      },
    );
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /Instagram user stomer/ })
      .first()
      .click();
    await expect(
      page.getByText("Human Agent available for a real manual support reply"),
    ).toBeVisible();
    await expect(page.getByText("Send as Human Agent")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Send reply" }),
    ).toBeDisabled();
    await page.getByLabel("Send as Human Agent").check();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("Human support extension reply");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("Human support extension reply"),
    ).toBeVisible();
  });

  test("customer message reopens the window and token expiry reconnects", async ({
    page,
  }) => {
    await login(page);
    const tenant = await tenantContext(page.request);
    const connected = await connection(page.request, tenant.id);
    await page.request.post(
      `${api}/integrations/instagram/${connected.id}/test-event/`,
      {
        headers: tenant.headers,
        data: {
          event_type: "message",
          sender_id: "ig_e2e_customer",
          message_id: "ig_e2e_reopen",
          text: "Customer reopens window",
        },
      },
    );
    await page.request.post(
      `${api}/integrations/instagram/${connected.id}/test-control/`,
      { headers: tenant.headers, data: { action: "expire_token" } },
    );
    await page.goto(`/en/app/settings/channels/instagram/${connected.id}`);
    await expect(page.getByText("expired", { exact: true })).toBeVisible();
    await expect(page.getByText("Expired or missing")).toBeVisible();
    await page.getByRole("button", { name: "Disconnect" }).click();
    await page.getByRole("button", { name: "Reconnect" }).click();
    await expect(page.getByText("connected", { exact: true })).toBeVisible();
  });

  test("tenant switch and lower role never inherit owner Instagram state", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/instagram");
    await expect(page.getByText("@althair_demo")).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(
      page.getByText("No Instagram account connected"),
    ).toBeVisible();
    await page.context().clearCookies();
    await login(page, "member@portal.test");
    await page.goto("/en/app/settings/channels/instagram");
    await expect(
      page.getByRole("button", { name: "Connect Instagram" }),
    ).toHaveCount(0);
  });

  test("RU, UZ, EN and mobile setup remain accessible and secret-free", async ({
    page,
  }) => {
    await login(page);
    for (const [locale, heading] of [
      ["ru", "Сообщения Instagram"],
      ["uz", "Instagram xabarlari"],
      ["en", "Instagram Messaging"],
    ] as const) {
      await page.goto(`/${locale}/app/settings/channels/instagram`);
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/settings/channels/instagram");
    await expect(page.getByText("@althair_demo")).toBeVisible();
    const content = await page.locator("body").innerText();
    expect(content).not.toContain("META_APP_SECRET");
    expect(content).not.toContain("fake-access");
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
});
