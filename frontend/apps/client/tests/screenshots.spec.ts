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
    page.getByRole("heading", { name: "Company overview" }),
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
    path: resolve(screenshotDir, "overview.png"),
    fullPage: true,
    style: screenshotStyle,
  });
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
