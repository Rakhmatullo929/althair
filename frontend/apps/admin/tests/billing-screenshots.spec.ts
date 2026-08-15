import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/billing-admin",
);

async function login(page: Page) {
  await page.goto("/en/login");
  await page.getByLabel("Internal email").fill("platform-owner@example.test");
  await page.getByLabel("Password").fill("internal-platform-development-only");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByLabel("Verification code").fill("000000");
  await page
    .getByRole("button", { name: "Verify and open operations" })
    .click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Platform overview" }),
  ).toBeVisible();
}

async function shot(page: Page, name: string) {
  await page.screenshot({ path: resolve(screenshotDir, name), fullPage: true });
}

test("@screenshots Internal Billing evidence", async ({ page }) => {
  test.setTimeout(180_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.goto("/en/app/billing/plans");
  await shot(page, "01-plan-catalog.png");
  await page.goto("/en/app/billing/subscriptions");
  await shot(page, "02-subscriptions.png");
  await page.getByRole("button", { name: "Grant manual" }).first().click();
  await shot(page, "03-manual-grant-mfa.png");
  await page.getByRole("button", { name: "Close" }).click();
  await page.goto("/en/app/billing/invoices");
  await shot(page, "04-invoices.png");
  await page
    .getByRole("button", { name: "Reviewed mark paid" })
    .first()
    .click();
  await shot(page, "05-reviewed-payment.png");
  await page.getByRole("button", { name: "Close" }).click();
  await page.goto("/en/app/billing/usage");
  await shot(page, "06-usage-provider-events.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ru/app/billing/plans");
  await shot(page, "07-mobile-ru.png");
});
