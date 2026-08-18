import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/billing-admin",
);
const internal = "http://localhost:8012/api/v1/internal";

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
  await page.goto("/en/login");
  await shot(page, "00-bootstrap-admin-login.png");
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
  await page.goto("/en/app/billing/wallets");
  const walletCard = page.locator(".billing-admin-card", {
    hasText: "Mehr Clinic",
  });
  await walletCard.getByText("Recent immutable ledger entries").click();
  await shot(page, "07-company-balances.png");
  await walletCard.getByRole("button", { name: "Top up" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("Amount in minor units").fill("100000");
  await dialog
    .getByLabel("Review reason")
    .fill("Synthetic screenshot wallet top up approval");
  await shot(page, "08-wallet-top-up-confirmation.png");
  await dialog.getByRole("button", { name: "Confirm reviewed action" }).click();
  await expect(
    page.getByText("Reviewed Billing action completed and audited."),
  ).toBeVisible();
  await walletCard.getByText("Recent immutable ledger entries").click();
  await shot(page, "09-due-invoice-auto-application.png");

  const walletsResponse = await page.request.get(
    `${internal}/billing/wallets/`,
  );
  expect(walletsResponse.ok()).toBeTruthy();
  const wallets = (await walletsResponse.json()) as {
    results: Array<{
      organization_name: string;
      recent_transactions: Array<{ id: string; transaction_type: string }>;
    }>;
  };
  const topUp = wallets.results
    .find((wallet) => wallet.organization_name === "Mehr Clinic")
    ?.recent_transactions.find(
      (transaction) => transaction.transaction_type === "top_up",
    );
  expect(topUp).toBeTruthy();
  await walletCard.getByRole("button", { name: "Reverse entry" }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("Ledger transaction UUID").fill(topUp!.id);
  await dialog
    .getByLabel("Review reason")
    .fill("Synthetic screenshot compensating reversal approval");
  await shot(page, "10-wallet-reversal-confirmation.png");
  await dialog.getByRole("button", { name: "Confirm reviewed action" }).click();
  await expect(
    page.getByText("Reviewed Billing action completed and audited."),
  ).toBeVisible();
  await walletCard.getByText("Recent immutable ledger entries").click();
  await shot(page, "11-wallet-reversal-ledger.png");

  await walletCard.getByRole("button", { name: "Freeze", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog
    .getByLabel("Review reason")
    .fill("Synthetic screenshot wallet freeze approval");
  await shot(page, "12-wallet-freeze-confirmation.png");
  await dialog.getByRole("button", { name: "Confirm reviewed action" }).click();
  await expect(
    page.getByText("Reviewed Billing action completed and audited."),
  ).toBeVisible();
  await shot(page, "13-frozen-wallet.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ru/app/billing/wallets");
  await shot(page, "14-wallet-mobile-ru.png");
});
