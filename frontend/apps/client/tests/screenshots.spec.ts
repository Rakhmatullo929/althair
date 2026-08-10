import { expect, test, type Page } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/client",
);
const screenshotStyle = "nextjs-portal { display: none !important; }";

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

test("@screenshots client portal evidence", async ({ page }) => {
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/en/login");
  await expect(
    page.getByRole("heading", { name: "Sign in to your workspace" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "login-desktop.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(screenshotDir, "login-mobile.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.screenshot({
    path: resolve(screenshotDir, "updated-overview.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto("/en/app/inbox");
  await page.locator(".conversation-list > li > button").first().click();
  await expect(page.locator(".message-timeline")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "inbox-desktop.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/en/app/inbox");
  await expect(page.locator(".conversation-list-panel")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "inbox-mobile-list.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.locator(".conversation-list > li > button").first().click();
  await page.screenshot({
    path: resolve(screenshotDir, "inbox-mobile-conversation.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/en/app/contacts");
  await expect(page.locator(".contact-detail-panel h2")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "contact-detail.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByRole("button", { name: "Merge" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "contact-merge-dialog.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page.goto("/en/app/leads");
  await expect(page.locator(".kanban-board")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "leads-kanban.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Accessible list" }).click();
  await page.screenshot({
    path: resolve(screenshotDir, "leads-mobile-list.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/en/app/tasks");
  await page.screenshot({
    path: resolve(screenshotDir, "tasks.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByLabel("Organization").selectOption({
    label: "Atlas Academy · Administrator",
  });
  await page.goto("/en/app/inbox");
  await expect(page.getByText("Inbox is clear")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "inbox-empty.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByLabel("Organization").selectOption({
    label: "Paused Studio · Owner",
  });
  await expect(page.getByText(/organization is suspended/i)).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "crm-suspended-read-only.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByLabel("Organization").selectOption({
    label: "Mehr Clinic · Owner",
  });
  for (const [locale, name] of [
    ["ru", "inbox-ru.png"],
    ["uz", "inbox-uz.png"],
  ] as const) {
    await page.goto(`/${locale}/app/inbox`);
    await page.screenshot({
      path: resolve(screenshotDir, name),
      fullPage: true,
      style: screenshotStyle,
    });
  }
  await page.goto("/en/app");
  for (const [path, name, heading] of [
    [
      "/en/app/onboarding",
      "onboarding-desktop.png",
      "Set up your front office",
    ],
    ["/en/app/settings/company", "company-settings.png", "Company settings"],
    ["/en/app/settings/team", "team.png", "Team and invitations"],
    ["/en/app/settings/branches", "branches.png", "Branches"],
    ["/en/app/settings/channels", "channels.png", "Channel status"],
    ["/en/app/settings/ai-context", "ai-context-draft.png", "AI Context"],
  ] as const) {
    await page.goto(path);
    await expect(
      page.getByRole("heading", { name: heading, exact: true }).first(),
    ).toBeVisible();
    await page.screenshot({
      path: resolve(screenshotDir, name),
      fullPage: name !== "ai-context-draft.png",
      style: screenshotStyle,
    });
  }
  await page.goto("/en/app/onboarding");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(screenshotDir, "onboarding-mobile.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/en/app/settings/ai-context");
  await page.getByRole("button", { name: "Publish version" }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "Publish version" })
    .click();
  await expect(page.getByText("published", { exact: true })).toBeVisible();
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: resolve(screenshotDir, "ai-context-published.png"),
    fullPage: false,
    style: screenshotStyle,
  });
});
