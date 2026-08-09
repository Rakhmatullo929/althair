import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const seedEmail = "owner@portal.test";
const seedPassword = "client-portal-development-only-password";

async function login(page: Page, email = seedEmail, password = seedPassword) {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/en\/app/);
  await expect(
    page.getByRole("heading", { name: "Company overview" }),
  ).toBeVisible();
}

test.describe.serial("client portal journeys", () => {
  test("registers and completes resumable onboarding", async ({ page }) => {
    await page.goto("/en/register");
    await page.getByLabel("First name").fill("E2E");
    await page.getByLabel("Last name").fill("Owner");
    await page.getByLabel("Work email").fill("e2e-owner@portal.test");
    await page
      .getByLabel("Create password", { exact: true })
      .fill("E2E-customer-password-2026!");
    await page.getByLabel("Company name").fill("E2E Company");
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page).toHaveURL(/\/en\/app\/onboarding/);
    await page.getByLabel("Public business name *").fill("E2E Company");
    await page.getByRole("button", { name: /Save and continue/ }).click();
    await page
      .getByLabel("Concise description *")
      .fill("A deterministic E2E company.");
    await page
      .getByLabel("Detailed business description *")
      .fill("The company validates the complete client onboarding workflow.");
    await page
      .getByLabel("Key products and services *")
      .fill("Portal onboarding verification.");
    await page.getByRole("button", { name: /Save and continue/ }).click();
    await page.getByLabel("Branch name *").fill("Main branch");
    await page.getByLabel("Address").fill("1 Test Street");
    await page.getByRole("button", { name: /Save and continue/ }).click();
    await page.getByRole("button", { name: /Save and continue/ }).click();
    await page.getByLabel("Assistant name *").fill("E2E Assistant");
    await page
      .getByLabel("Introduction *")
      .fill("I am the E2E Company's digital front-office assistant.");
    await page
      .getByLabel("Fallback response *")
      .fill("A team member will help with that request.");
    await page.getByRole("button", { name: /Save and continue/ }).click();
    await page.getByRole("button", { name: /Finish onboarding/ }).click();
    await expect(page).toHaveURL(/\/en\/app\/?$/);
    await expect(
      page.getByText("Your essential setup is 100% complete."),
    ).toBeVisible();
  });

  test("logs out and back in with HttpOnly cookie auth", async ({ page }) => {
    await login(page);
    await page.locator(".user-menu summary").click();
    await page.getByRole("button", { name: "Log out" }).click();
    await expect(page).toHaveURL(/\/en\/login/);
    await page.getByLabel("Email").fill(seedEmail);
    await page.getByLabel("Password", { exact: true }).fill(seedPassword);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Company overview" }),
    ).toBeVisible();
  });

  test("switches organizations without stale tenant data", async ({ page }) => {
    await login(page);
    await expect(
      page.getByRole("heading", { name: "Mehr Clinic" }),
    ).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(
      page.getByRole("heading", { name: "Atlas Academy" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Mehr Clinic" }),
    ).toHaveCount(0);
    await page.getByRole("link", { name: "Branches" }).click();
    await expect(page.getByText("Yunusobod Campus")).toBeVisible();
    await expect(page.getByText("Chilonzor")).toHaveCount(0);
  });

  test("owner invites a member and invitation is accepted atomically", async ({
    page,
    browser,
  }) => {
    await login(page);
    await page.getByRole("link", { name: "Team" }).click();
    await page.getByRole("button", { name: "Invite member" }).click();
    await page.getByLabel("Email").fill("invited-e2e@portal.test");
    await page.locator('select[name="role"]').selectOption("viewer");
    await page.getByRole("button", { name: "Create invitation" }).click();
    const inviteUrl = await page
      .locator(".development-link code")
      .textContent();
    expect(inviteUrl).toBeTruthy();
    const context = await browser.newContext();
    const invitee = await context.newPage();
    const localizedInviteUrl = new URL(inviteUrl!);
    localizedInviteUrl.pathname = localizedInviteUrl.pathname.replace(
      /^\/(ru|uz|en)\//,
      "/en/",
    );
    await invitee.goto(localizedInviteUrl.toString());
    await invitee.getByLabel("First name").fill("Invited");
    await invitee.getByLabel("Last name").fill("Member");
    await invitee
      .getByLabel("Create password", { exact: true })
      .fill("Invited-member-password-2026!");
    await invitee.getByRole("button", { name: "Accept invitation" }).click();
    await expect(invitee).toHaveURL(/\/en\/app/);
    await context.close();
  });

  test("lower role cannot perform owner actions", async ({ page }) => {
    await login(page, "member@portal.test", seedPassword);
    await page.getByRole("link", { name: "Team" }).click();
    await expect(
      page.getByRole("button", { name: "Invite member" }),
    ).toHaveCount(0);
    await expect(page.getByText(/review the team/)).toBeVisible();
    await page.getByRole("link", { name: "AI Context" }).click();
    await expect(
      page.getByRole("button", { name: "Publish version" }),
    ).toHaveCount(0);
  });

  test("creates and edits a branch", async ({ page }) => {
    await login(page);
    await page.getByRole("link", { name: "Branches" }).click();
    await page.getByRole("button", { name: "Add branch" }).click();
    await page.getByLabel("Branch name").fill("Mirzo Ulugbek");
    await page.getByLabel("Address").fill("Buyuk Ipak Yo‘li 10");
    await page.getByRole("button", { name: "Save branch" }).click();
    const card = page.locator(".branch-card", { hasText: "Mirzo Ulugbek" });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Edit" }).click();
    await page.getByLabel("Branch name").fill("Mirzo Ulugbek Center");
    await page.getByRole("button", { name: "Save branch" }).click();
    await expect(page.getByText("Mirzo Ulugbek Center")).toBeVisible();
  });

  test("saves AI Context draft and publishes a version", async ({ page }) => {
    await login(page);
    await page.getByRole("link", { name: "AI Context" }).click();
    await page
      .getByLabel("Concise business summary")
      .fill("A published E2E clinic context.");
    await expect(
      page.getByText("You have unsaved draft changes."),
    ).toBeVisible();
    await page.getByRole("button", { name: "Save draft" }).click();
    await expect(page.getByText("You have unsaved draft changes.")).toHaveCount(
      0,
    );
    await page.getByRole("button", { name: "Publish version" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Publish version" })
      .click();
    await expect(page.getByText("published", { exact: true })).toBeVisible();
  });

  test("suspended organization is visibly read-only", async ({ page }) => {
    await login(page);
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Paused Studio · Owner" });
    await expect(page.getByText(/organization is suspended/i)).toBeVisible();
    await page.getByRole("link", { name: "Company" }).click();
    await expect(
      page.getByRole("button", { name: "Save changes" }),
    ).toHaveCount(0);
  });

  test("RU, UZ, EN and accessibility smoke", async ({ page }) => {
    for (const [locale, title] of [
      ["ru", "Войдите в рабочее пространство"],
      ["uz", "Ish maydoniga kiring"],
      ["en", "Sign in to your workspace"],
    ] as const) {
      await page.goto(`/${locale}/login`);
      await expect(page.getByRole("heading", { name: title })).toBeVisible();
    }
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });

  test("desktop and mobile navigation are usable", async ({ page }) => {
    await login(page);
    await expect(page.locator(".sidebar")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator(".sidebar")).toBeHidden();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(
      page.getByRole("dialog").getByRole("link", { name: "Company" }),
    ).toBeVisible();
    await page.goto("/en/app/onboarding");
    const viewport = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(viewport.scrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(
      viewport.clientWidth,
    );
  });
});
