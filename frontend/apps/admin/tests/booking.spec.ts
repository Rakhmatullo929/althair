import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/en/login");
  await page.getByLabel("Internal email").fill("platform-owner@example.test");
  await page.getByLabel("Password").fill("internal-platform-development-only");
  await page.getByRole("button", { name: "Continue securely" }).click();
  await page.getByLabel("Verification code").fill("000000");
  await page
    .getByRole("button", { name: "Verify and open operations" })
    .click();
}

test("internal Booking shows only tenant-safe aggregates", async ({ page }) => {
  await login(page);
  await page.goto("/en/app/booking");
  await expect(
    page.getByRole("heading", { name: "Booking operations" }),
  ).toBeVisible();
  await expect(page.getByText("Mehr Clinic")).toBeVisible();
  await expect(page.getByText(/No customer notes/)).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
