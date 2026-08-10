import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const visitor = "Widget E2E Visitor";
const api = "http://localhost:8011/api/v1";
const publicKey = "wc_demo_portal_test";
const publicOrigin = "http://localhost:3001";

async function createPublicSession(page: Page) {
  const configResponse = await page.request.get(
    `${api}/public/web-chat/installations/${publicKey}/config/`,
    { headers: { Origin: publicOrigin } },
  );
  expect(configResponse.status()).toBe(200);
  const config = (await configResponse.json()) as { origin_proof: string };
  const sessionResponse = await page.request.post(
    `${api}/public/web-chat/installations/${publicKey}/sessions/`,
    {
      headers: { Origin: publicOrigin },
      data: {
        origin_proof: config.origin_proof,
        consent_accepted: true,
        language: "en",
      },
    },
  );
  expect(sessionResponse.status()).toBe(201);
  return (await sessionResponse.json()) as {
    session_id: string;
    session_token: string;
    expires_at: string;
  };
}

function sessionHeaders(token: string) {
  return { Origin: publicOrigin, Authorization: `Bearer ${token}` };
}

async function startWidget(page: Page, locale = "en", name = visitor) {
  await page.goto(`/${locale}/demo`);
  await expect(
    page.getByText("Public Web Chat", { exact: true }),
  ).toBeVisible();
  await page
    .getByLabel(locale === "ru" ? "Имя" : locale === "uz" ? "Ism" : "Name")
    .fill(name);
  if (locale === "en")
    await page
      .getByLabel("Email")
      .fill(`${name.toLowerCase().replaceAll(" ", "-")}@example.test`);
  await page.locator(".webchat-consent input").check();
  await page
    .getByRole("button", {
      name:
        locale === "ru"
          ? "Начать диалог"
          : locale === "uz"
            ? "Suhbatni boshlash"
            : "Start conversation",
    })
    .click();
  await expect(page.locator(".webchat-composer")).toBeVisible();
}

async function login(page: Page, email = "owner@portal.test") {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page
    .getByLabel("Password", { exact: true })
    .fill("client-portal-development-only-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

test.describe.serial("public Web Chat journey", () => {
  test("public transport enforces origin, session scope, idempotency, SSE cursor, and token rotation", async ({
    page,
  }) => {
    const blocked = await page.request.get(
      `${api}/public/web-chat/installations/${publicKey}/config/`,
      { headers: { Origin: "https://not-allowed.example.test" } },
    );
    expect(blocked.status()).toBe(403);

    const spoofedTenant = await page.request.get(
      `${api}/public/web-chat/installations/${publicKey}/config/`,
      {
        headers: {
          Origin: publicOrigin,
          "X-Organization-ID": "00000000-0000-0000-0000-000000000000",
        },
      },
    );
    expect(spoofedTenant.status()).toBe(200);
    expect(await spoofedTenant.text()).not.toContain("organization");

    const firstSession = await createPublicSession(page);
    const secondSession = await createPublicSession(page);
    const messageUrl = `${api}/public/web-chat/sessions/${firstSession.session_id}/messages/`;
    const messageHeaders = {
      ...sessionHeaders(firstSession.session_token),
      "Idempotency-Key": "playwright-duplicate-message",
    };
    const first = await page.request.post(messageUrl, {
      headers: messageHeaders,
      data: { body: "Exactly once from Playwright" },
    });
    const duplicate = await page.request.post(messageUrl, {
      headers: messageHeaders,
      data: { body: "Exactly once from Playwright" },
    });
    expect(first.status()).toBe(201);
    expect(duplicate.status()).toBe(200);
    expect((await duplicate.json()).created).toBe(false);
    const messages = await page.request.get(`${messageUrl}?after=0`, {
      headers: sessionHeaders(firstSession.session_token),
    });
    const events = (await messages.json()) as {
      events: Array<{ message?: { body: string } }>;
    };
    expect(
      events.events.filter(
        (event) => event.message?.body === "Exactly once from Playwright",
      ),
    ).toHaveLength(1);

    const crossSession = await page.request.get(
      `${api}/public/web-chat/sessions/${secondSession.session_id}/messages/`,
      { headers: sessionHeaders(firstSession.session_token) },
    );
    expect(crossSession.status()).toBe(401);
    const tenantApi = await page.request.get(`${api}/web-chat/installations/`, {
      headers: {
        Authorization: `Bearer ${firstSession.session_token}`,
        "X-Organization-ID": "00000000-0000-0000-0000-000000000000",
      },
    });
    expect([401, 403]).toContain(tenantApi.status());

    const stream = await page.request.get(
      `${api}/public/web-chat/sessions/${firstSession.session_id}/events/`,
      {
        headers: {
          ...sessionHeaders(firstSession.session_token),
          "Last-Event-ID": "0",
        },
      },
    );
    expect(stream.status()).toBe(200);
    expect(stream.headers()["content-type"]).toContain("text/event-stream");
    expect(await stream.text()).toContain(": heartbeat");

    const resume = await page.request.post(
      `${api}/public/web-chat/sessions/${firstSession.session_id}/resume/`,
      { headers: sessionHeaders(firstSession.session_token) },
    );
    expect(resume.status()).toBe(200);
    const rotated = (await resume.json()) as { session_token: string };
    expect(rotated.session_token).not.toBe(firstSession.session_token);
    expect(
      (
        await page.request.get(messageUrl, {
          headers: sessionHeaders(firstSession.session_token),
        })
      ).status(),
    ).toBe(401);
    expect(
      (
        await page.request.get(messageUrl, {
          headers: sessionHeaders(rotated.session_token),
        })
      ).status(),
    ).toBe(200);
    await page.request.post(
      `${api}/public/web-chat/sessions/${firstSession.session_id}/close/`,
      { headers: sessionHeaders(rotated.session_token) },
    );
    expect(
      (
        await page.request.get(messageUrl, {
          headers: sessionHeaders(rotated.session_token),
        })
      ).status(),
    ).toBe(401);
  });

  test("visitor consents, identifies, sends, and receives deterministic AI", async ({
    page,
  }) => {
    await startWidget(page, "en", "Widget AI Visitor");
    await page
      .getByPlaceholder("Write a message…")
      .fill("Hello, what services are available?");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(
      page.getByText("Hello, what services are available?"),
    ).toBeVisible();
    await expect(page.getByText(/published information/i)).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/AI generated/)).toBeVisible();
    const results = await new AxeBuilder({ page })
      .include(".webchat-widget")
      .analyze();
    expect(results.violations).toEqual([]);
  });

  test("conversation appears in Unified Inbox and operator reply returns to widget", async ({
    page,
  }) => {
    await startWidget(page, "en", visitor);
    await page
      .getByPlaceholder("Write a message…")
      .fill("I need an operator follow-up.");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("I need an operator follow-up.")).toBeVisible();
    await page.reload();
    await expect(page.getByText("I need an operator follow-up.")).toBeVisible({
      timeout: 10_000,
    });
    await login(page);
    await page.goto("/en/app/inbox");
    await expect(page.getByText(visitor).first()).toBeVisible();
    await page
      .getByRole("button", { name: new RegExp(visitor) })
      .first()
      .click();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("A human operator has taken over this chat.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("A human operator has taken over this chat."),
    ).toBeVisible();
    await page.goto("/en/demo");
    await expect(
      page.getByText("A human operator has taken over this chat."),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("visitor can request human handoff and close securely", async ({
    page,
  }) => {
    await startWidget(page, "en", "Widget Handoff Visitor");
    await page
      .getByPlaceholder("Write a message…")
      .fill("Please connect me to a person.");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(
      page.getByText("Please connect me to a person."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Talk to a person" }).click();
    await expect(
      page.getByText("A team member will continue here."),
    ).toBeVisible();
    await login(page);
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /Widget Handoff Visitor/ })
      .first()
      .click();
    await expect(page.getByText("Human handoff required")).toBeVisible();
    await page.getByRole("button", { name: "Acknowledge" }).click();
    await page.getByRole("button", { name: "Resolve and resume" }).click();
    await expect(page.getByText("Human handoff required")).toHaveCount(0);
    await page.goto("/en/demo");
    await page.getByRole("button", { name: "Close chat" }).click();
    await expect(
      page.getByRole("button", { name: "Start conversation" }),
    ).toBeVisible();
  });

  test("RU, UZ, EN and mobile widget remain usable", async ({ page }) => {
    for (const [locale, button] of [
      ["ru", "Начать диалог"],
      ["uz", "Suhbatni boshlash"],
      ["en", "Start conversation"],
    ] as const) {
      await page.goto(`/${locale}/demo`);
      await expect(page.getByRole("button", { name: button })).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/demo");
    const viewport = await page.evaluate(() => ({
      width: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    expect(viewport.scroll).toBeLessThanOrEqual(viewport.width);
  });

  test("owner configures tenant installation without exposing tokens", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/web-chat");
    await expect(
      page.getByRole("heading", { name: "Public Web Chat" }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Configure" }).first().click();
    await expect(
      page.getByRole("heading", { name: "Visitor experience" }),
    ).toBeVisible();
    await expect(page.locator(".embed-code")).toContainText(
      "data-installation-key",
    );
    await expect(page.locator("body")).not.toContainText("session_token");
  });

  test("expired credentials fail safely in the widget", async ({ page }) => {
    await startWidget(page, "en", "Expired Session Visitor");
    await page.route("**/public/web-chat/sessions/*/messages/**", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "session_expired" } }),
      }),
    );
    await page.route("**/public/web-chat/sessions/*/events/**", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "session_expired" } }),
      }),
    );
    await expect(
      page.getByText("This session expired. Start a new conversation."),
    ).toBeVisible({ timeout: 7_000 });
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(
      page.getByRole("button", { name: "Start conversation" }),
    ).toBeVisible();
  });

  test("installation lifecycle, organization switching, suspension, and lower roles stay isolated", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/web-chat");
    const organization = page.getByLabel("Organization");
    await organization.selectOption({ label: "Atlas Academy · Administrator" });
    await expect(page.getByText("No Web Chat installation")).toBeVisible();
    await organization.selectOption({ label: "Paused Studio · Owner" });
    await expect(
      page.getByRole("button", { name: "New installation" }),
    ).toHaveCount(0);
    await organization.selectOption({ label: "Mehr Clinic · Owner" });
    await page.getByRole("button", { name: "New installation" }).click();
    await page.getByLabel("Display name").fill("Lifecycle Website");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    const card = page.locator(".webchat-installation-card").filter({
      hasText: "Lifecycle Website",
    });
    await expect(card).toBeVisible();
    const configureLink = card.getByRole("link", { name: "Configure" });
    const detailUrl = await configureLink.getAttribute("href");
    expect(detailUrl).toBeTruthy();
    await configureLink.click();
    const embed = page.locator(".embed-code");
    const originalEmbed = await embed.textContent();
    await page.getByRole("button", { name: "Activate" }).click();
    await expect(page.getByText("active", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByText("paused", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Activate" }).click();
    await page.getByRole("button", { name: "Rotate public key" }).click();
    await expect(embed).not.toHaveText(originalEmbed ?? "");
    await page.getByRole("button", { name: "Revoke" }).click();
    await expect(page.getByText("revoked", { exact: true })).toBeVisible();

    await page.locator(".user-menu summary").click();
    await page.getByRole("button", { name: "Log out" }).click();
    await login(page, "member@portal.test");
    await page.goto(detailUrl!);
    await expect(page.getByLabel("Display name")).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Rotate public key" }),
    ).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Revoke" })).toHaveCount(0);
  });
});
