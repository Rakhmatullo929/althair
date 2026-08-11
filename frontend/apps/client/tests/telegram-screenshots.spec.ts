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
  "../../artifacts/screenshots/telegram",
);

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

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(screenshotDir, name),
    fullPage: true,
    style: "nextjs-portal { display: none !important; }",
  });
}

test("@screenshots Telegram Managed Bots evidence", async ({ page }) => {
  test.setTimeout(120_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.goto("/en/app/settings/channels/telegram");
  await shot(page, "01-telegram-managed-setup.png");
  await page.getByRole("button", { name: "Create one-time link" }).click();
  await shot(page, "02-telegram-owner-link.png");
  await page
    .getByRole("button", { name: "Simulate owner confirmation" })
    .click();
  await page.getByLabel("Bot display name").fill("Mehr Clinic Support");
  await page.getByLabel("Bot username").fill("MehrClinicSupportBot");
  await page.getByRole("button", { name: "Continue to Telegram" }).click();
  await shot(page, "03-telegram-explicit-confirmation.png");
  await page
    .getByRole("button", { name: "Simulate managed-bot event" })
    .click();
  await expect(page.getByText("@MehrClinicSupportBot")).toBeVisible();
  await shot(page, "04-telegram-connected.png");

  const context = await tenant(page.request);
  const connections = (await (
    await page.request.get(`${api}/integrations/telegram/`, {
      headers: { "X-Organization-ID": context.id },
    })
  ).json()) as { results: Array<{ id: string }> };
  const id = connections.results[0].id;
  await page.goto(`/en/app/settings/channels/telegram/${id}`);
  await shot(page, "05-telegram-health-access.png");
  await page.request.post(`${api}/integrations/telegram/${id}/test-event/`, {
    headers: context.headers,
    data: {
      event_type: "message",
      update_id: 780001,
      message_id: 8001,
      telegram_user_id: 78001,
      first_name: "Telegram Screenshot",
      text: "Can you help me in Telegram?",
    },
  });
  await page.goto("/en/app/inbox");
  await page.getByRole("button", { name: /Telegram Screenshot/ }).click();
  await shot(page, "06-telegram-unified-inbox.png");
  await page.goto("/ru/app/settings/channels/telegram");
  await expect(
    page.getByRole("heading", { name: "Управляемые боты Telegram" }),
  ).toBeVisible();
  await shot(page, "07-telegram-ru.png");
  await page.goto("/uz/app/settings/channels/telegram");
  await expect(
    page.getByRole("heading", { name: "Telegram boshqariladigan botlari" }),
  ).toBeVisible();
  await shot(page, "08-telegram-uz.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/en/app/settings/channels/telegram");
  await expect(
    page.getByRole("heading", { name: "Telegram Managed Bots" }),
  ).toBeVisible();
  await shot(page, "09-telegram-mobile.png");
});
