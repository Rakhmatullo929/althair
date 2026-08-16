import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/booking",
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

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(screenshotDir, name),
    fullPage: true,
  });
}

test("@screenshots Booking workspace evidence", async ({ page }) => {
  test.setTimeout(180_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  for (const [path, name] of [
    ["", "01-calendar.png"],
    ["appointments", "02-appointment-list.png"],
    ["services", "04-service-catalog.png"],
    ["staff", "05-staff-services.png"],
    ["resources", "06-resources.png"],
    ["schedules", "07-schedule-editor.png"],
    ["waitlist", "08-waitlist.png"],
    ["reminders", "09-reminder-failure.png"],
    ["settings", "10-settings.png"],
  ]) {
    await page.goto(`/en/app/booking/${path}`);
    await shot(page, name);
  }

  await page.goto("/en/app/booking/appointments");
  await page.locator(".appointment-card").first().click();
  await expect(page.locator(".appointment-detail-card")).toBeVisible();
  await shot(page, "03-appointment-detail.png");

  await page.goto("/en/app/inbox");
  await page.locator(".conversation-list button").first().click();
  await page.locator(".inbox-booking-toggle").click();
  await expect(page.locator(".inbox-booking-body")).toBeVisible();
  await shot(page, "11-ai-booking-inbox.png");

  await page.goto("/en/app/booking/settings");
  await page.getByRole("link", { name: "Preview" }).click();
  await shot(page, "12-public-booking.png");
  await page.getByRole("button", { name: /Initial consultation/ }).click();
  await page.getByLabel("Location").selectOption({ index: 1 });
  await page.getByRole("button", { name: "Find available times" }).click();
  await expect(page.locator(".slot-grid button").first()).toBeVisible();
  await shot(page, "13-real-availability.png");
  await page.locator(".slot-grid button").first().click();
  await page.getByLabel("Full name").fill("Screenshot Booking Customer");
  await page.getByLabel("Email").fill("booking-screenshot@example.test");
  await page.getByRole("checkbox").check();
  await shot(page, "14-create-appointment.png");
  await page.getByRole("button", { name: "Reserve appointment" }).click();
  await expect(
    page.getByRole("heading", { name: "Your request is saved" }),
  ).toBeVisible();
  await shot(page, "15-authoritative-confirmation.png");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ru/app/booking");
  await shot(page, "16-mobile-agenda-ru.png");
  await page.goto("/uz/app/booking");
  await shot(page, "17-mobile-agenda-uz.png");
});
