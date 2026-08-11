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
  ).json()) as {
    csrftoken: string;
  };
  return {
    id,
    headers: { "X-Organization-ID": id, "X-CSRFToken": csrf.csrftoken },
  };
}

async function telegramConnection(
  request: APIRequestContext,
  organizationId: string,
) {
  const response = await request.get(`${api}/integrations/telegram/`, {
    headers: { "X-Organization-ID": organizationId },
  });
  const payload = (await response.json()) as {
    results: Array<{ id: string; bot_username: string }>;
  };
  return payload.results[0];
}

test.describe.serial("Telegram Managed Bots", () => {
  test("owner links identity and explicitly confirms managed-bot creation", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/telegram");
    await expect(
      page.getByRole("heading", { name: "Telegram Managed Bots" }),
    ).toBeVisible();
    await expect(page.getByText("No Telegram bot connected")).toBeVisible();
    await page.getByRole("button", { name: "Create one-time link" }).click();
    const managerLink = page.getByRole("link", {
      name: /Open Telegram manager/,
    });
    await expect(managerLink).toHaveAttribute(
      "href",
      /t\.me\/AlthairManagerBot\?start=link_/,
    );
    await page
      .getByRole("button", { name: "Simulate owner confirmation" })
      .click();
    await expect(page.getByText("linked", { exact: true })).toBeVisible();
    await page.getByLabel("Bot display name").fill("Mehr Clinic Support");
    await page.getByLabel("Bot username").fill("MehrClinicSupportBot");
    await page.getByRole("button", { name: "Continue to Telegram" }).click();
    await expect(
      page.getByRole("link", { name: /Confirm creation in Telegram/ }),
    ).toHaveAttribute(
      "href",
      /t\.me\/newbot\/AlthairManagerBot\/MehrClinicSupportBot/,
    );
    await page
      .getByRole("button", { name: "Simulate managed-bot event" })
      .click();
    await expect(
      page.getByRole("heading", { name: "@MehrClinicSupportBot" }),
    ).toBeVisible();
    const body = await page.locator("body").innerText();
    expect(body).not.toContain("bot_token");
    expect(body).not.toContain("webhook_secret");
    expect(body).not.toContain("test-only-");
  });

  test("private inbound message creates CRM contact and Inbox reply", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const connection = await telegramConnection(page.request, context.id);
    const inbound = await page.request.post(
      `${api}/integrations/telegram/${connection.id}/test-event/`,
      {
        headers: context.headers,
        data: {
          event_type: "message",
          update_id: 7001001,
          message_id: 1001,
          telegram_user_id: 721001,
          username: "telegram_e2e_customer",
          first_name: "Telegram E2E Customer",
          text: "Hello from the managed Telegram bot",
        },
      },
    );
    expect(inbound.status()).toBe(202);
    const duplicate = await page.request.post(
      `${api}/integrations/telegram/${connection.id}/test-event/`,
      {
        headers: context.headers,
        data: {
          event_type: "message",
          update_id: 7001001,
          message_id: 1001,
          telegram_user_id: 721001,
          text: "duplicate",
        },
      },
    );
    expect((await duplicate.json()).duplicates).toBe(1);
    await page.goto("/en/app/inbox");
    await page.getByRole("button", { name: /Telegram E2E Customer/ }).click();
    await expect(
      page.getByText("Hello from the managed Telegram bot"),
    ).toBeVisible();
    await expect(page.getByText("Telegram reply is available")).toBeVisible();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("Manual reply over Telegram");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("Manual reply over Telegram"),
    ).toBeVisible();
    await page.goto("/en/app/contacts");
    await expect(page.getByText("Telegram").first()).toBeVisible();
  });

  test("health, managed access, token rotation, and connection states are operator controlled", async ({
    page,
  }) => {
    await login(page);
    const context = await tenant(page.request);
    const connection = await telegramConnection(page.request, context.id);
    await page.goto(`/en/app/settings/channels/telegram/${connection.id}`);
    await expect(page.getByText("Encrypted · write-only")).toBeVisible();
    await page.getByRole("button", { name: "Run health check" }).click();
    await page.getByLabel("Restrict management access").click();
    await expect(page.getByLabel("Restrict management access")).toBeChecked();
    await page
      .getByLabel(/Additional Telegram user IDs/)
      .fill("700002, 700003");
    await page.getByRole("button", { name: "Save access" }).click();
    page.once("dialog", (dialog) => dialog.accept());
    await page.getByRole("button", { name: "Rotate token" }).click();
    await expect(page.getByText("2", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(page.getByText("paused", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Reconnect" }).click();
    await expect(page.getByText("connected", { exact: true })).toBeVisible();
  });

  test("tenant switching and lower roles never inherit Telegram ownership", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/telegram");
    await expect(page.getByText("@MehrClinicSupportBot")).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(page.getByText("No Telegram bot connected")).toBeVisible();
    await page.context().clearCookies();
    await login(page, "member@portal.test");
    await page.goto("/en/app/settings/channels/telegram");
    await expect(
      page.getByRole("button", { name: "Create one-time link" }),
    ).toHaveCount(0);
  });

  test("RU, UZ, EN and mobile UI are accessible and secret-free", async ({
    page,
  }) => {
    await login(page);
    for (const [locale, title] of [
      ["ru", "Управляемые боты Telegram"],
      ["uz", "Telegram boshqariladigan botlari"],
      ["en", "Telegram Managed Bots"],
    ] as const) {
      await page.goto(`/${locale}/app/settings/channels/telegram`);
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/settings/channels/telegram");
    const body = await page.locator("body").innerText();
    expect(body).not.toContain("TELEGRAM_MANAGER_BOT_TOKEN");
    expect(body).not.toContain("webhook_secret");
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });
});
