import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/booking-admin",
);

test("@screenshots Internal Booking evidence", async ({ page }) => {
  await mkdir(screenshotDir, { recursive: true });
  await page.goto("/en/login");
  await page.getByLabel("Internal email").fill("platform-owner@example.test");
  await page.getByLabel("Password").fill("internal-platform-development-only");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByLabel("Verification code").fill("000000");
  await page
    .getByRole("button", { name: "Verify and open operations" })
    .click();
  await page.goto("/en/app/booking");
  await expect(page.getByText("Mehr Clinic")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "01-booking-health.png"),
    fullPage: true,
  });
});
