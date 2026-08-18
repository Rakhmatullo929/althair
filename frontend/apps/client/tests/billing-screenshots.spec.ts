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
  const currentPlan = page.locator("article", {
    has: page.getByRole("heading", { name: "Starter", exact: true }),
  });
  await expect(currentPlan.getByText("Active", { exact: true })).toBeVisible();
  await shot(page, "01-customer-overview-wallet-active.png");
  await page.goto("/en/app/billing/plans");
  await shot(page, "02-plans.png");
  await page
    .locator(".billing-plan", { hasText: "Growth" })
    .getByRole("button", { name: "Preview change" })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await shot(page, "03-plan-change-preview.png");
  const usageResponse = page.waitForResponse((response) =>
    response.url().includes("/api/v1/billing/usage/"),
  );
  await page.goto("/en/app/billing/usage");
  const seededUsage = (await (await usageResponse).json()) as {
    results: Array<Record<string, unknown>>;
    [key: string]: unknown;
  };
  await shot(page, "04-usage.png");
  await page.goto("/en/app/billing/invoices");
  await shot(page, "05-paid-and-open-invoices.png");
  await page
    .locator("tr", { hasText: /paid/i })
    .getByRole("link", { name: "View" })
    .click();
  await shot(page, "06-paid-invoice-from-wallet.png");
  await page.goto("/en/app/billing/wallet");
  await expect(
    page.getByText("Balance history", { exact: true }),
  ).toBeVisible();
  await shot(page, "07-company-balance-and-ledger.png");
  await page.route("**/api/v1/billing/wallet/", async (route) => {
    const response = await route.fetch();
    const overview = await response.json();
    await route.fulfill({
      response,
      json: {
        ...overview,
        wallet: {
          ...overview.wallet,
          available_balance_minor: 0,
          low_balance: true,
        },
      },
    });
  });
  await page.goto("/en/app/billing/wallet?evidence=low-balance");
  await expect(page.getByText("Balance is low")).toBeVisible();
  await shot(page, "08-low-balance.png");
  await page.unroute("**/api/v1/billing/wallet/");
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
  await shot(page, "09-grace-state.png");
  await page.unroute("**/api/v1/billing/subscription/");
  await page.route("**/api/v1/billing/usage/", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      json: {
        ...seededUsage,
        results: [
          {
            ...seededUsage.results[0],
            quantity: "1000",
            included: 1000,
            remaining: "0",
          },
          ...seededUsage.results.slice(1),
        ],
      },
    });
  });
  await page.goto("/en/app/billing/usage");
  await shot(page, "10-limit-reached.png");
  await page.unroute("**/api/v1/billing/usage/");
  await page.setViewportSize({ width: 390, height: 844 });
  for (const [locale, name] of [
    ["en", "11-mobile-en.png"],
    ["ru", "12-mobile-ru.png"],
    ["uz", "13-mobile-uz.png"],
  ] as const) {
    await page.goto(`/${locale}/app/billing/wallet`);
    await shot(page, name);
  }
});
