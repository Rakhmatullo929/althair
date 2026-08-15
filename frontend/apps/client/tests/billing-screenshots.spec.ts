import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/billing",
);
const style = "nextjs-portal { display: none !important; }";

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

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(screenshotDir, name),
    fullPage: true,
    style,
  });
}

test("@screenshots customer Billing evidence", async ({ page }) => {
  test.setTimeout(180_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.goto("/en/app/billing");
  await expect(page.getByText("Trial is active")).toBeVisible();
  await shot(page, "01-customer-overview-trial.png");
  await page.goto("/en/app/billing/plans");
  await shot(page, "02-plans.png");
  await page
    .locator(".billing-plan", { hasText: "Growth" })
    .getByRole("button", { name: "Preview change" })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await shot(page, "03-plan-change-preview.png");
  await page.goto("/en/app/billing/usage");
  await shot(page, "04-usage.png");
  await page.goto("/en/app/billing/invoices");
  await page.getByRole("link", { name: "View" }).first().click();
  await shot(page, "05-invoice.png");
  await page.route("**/api/v1/billing/subscription/", async (route) => {
    const response = await route.fetch();
    const subscription = await response.json();
    await route.fulfill({
      response,
      json: {
        ...subscription,
        status: "grace",
        grace_ends_at: subscription.current_period_end,
      },
    });
  });
  await page.goto("/en/app/billing");
  await expect(page.getByText("Account is in a grace period")).toBeVisible();
  await shot(page, "06-grace-state.png");
  await page.unroute("**/api/v1/billing/subscription/");
  await page.route("**/api/v1/billing/usage/", async (route) => {
    const response = await route.fetch();
    const usage = await response.json();
    await route.fulfill({
      response,
      json: {
        ...usage,
        results: [
          {
            ...usage.results[0],
            quantity: "1000",
            included: 1000,
            remaining: "0",
          },
          ...usage.results.slice(1),
        ],
      },
    });
  });
  await page.goto("/en/app/billing/usage");
  await shot(page, "07-limit-reached.png");
  await page.unroute("**/api/v1/billing/usage/");
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [locale, name] of [
    ["en", "08-mobile-en.png"],
    ["ru", "09-mobile-ru.png"],
    ["uz", "10-mobile-uz.png"],
  ] as const) {
    await page.goto(`/${locale}/app/billing`);
    await shot(page, name);
  }
});
