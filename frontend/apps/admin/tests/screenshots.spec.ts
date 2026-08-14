import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/internal-super-admin",
);
const backend = "http://localhost:8012/api/v1/internal";

async function shot(page: Page, name: string) {
  await page.screenshot({ path: resolve(screenshotDir, name), fullPage: true });
}

test("@screenshots internal super admin evidence", async ({ page }) => {
  test.setTimeout(180_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/en/login");
  await page.getByRole("heading", { name: "Internal staff sign in" }).waitFor();
  await page.waitForLoadState("networkidle");
  await shot(page, "01-internal-login.png");
  await page.getByLabel("Internal email").fill("platform-owner@example.test");
  await page.getByLabel("Password").fill("internal-platform-development-only");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await shot(page, "02-internal-mfa.png");
  await page.getByLabel("Verification code").fill("000000");
  await page
    .getByRole("button", { name: "Verify and open operations" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Platform overview", level: 1 }),
  ).toBeVisible();
  await shot(page, "03-overview.png");
  const routes = [
    ["organizations", "04-organization-directory.png"],
    ["providers", "06-provider-health.png"],
    ["ai", "07-ai-usage-control.png"],
    ["jobs", "09-jobs-dead-letters.png"],
    ["incidents", "10-incident.png"],
    ["entitlements", "11-entitlement.png"],
    ["data-requests", "12-data-request.png"],
    ["audit", "13-audit.png"],
  ] as const;
  for (const [route, filename] of routes) {
    await page.goto(`/en/app/${route}`);
    await page.waitForLoadState("networkidle");
    await shot(page, filename);
  }
  const organizations = (await (
    await page.request.get(`${backend}/organizations/`)
  ).json()) as { results: Array<{ id: string }> };
  await page.goto(`/en/app/organizations/${organizations.results[0].id}`);
  await expect(page.getByText("Read-only internal context")).toBeVisible();
  await shot(page, "05-organization-detail.png");
  await page.goto("/en/app");
  await page.getByText("Global AI safety control").click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await shot(page, "08-emergency-switch-confirmation.png");
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await shot(page, "14-mobile-urgent-view.png");
});
