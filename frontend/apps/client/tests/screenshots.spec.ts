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

async function createAIInquiry(page: Page, name: string, body: string) {
  await page.goto("/en/app/inbox");
  await page.getByRole("button", { name: "Test conversation" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Display name").fill(name);
  await dialog.getByLabel("Inbound message").fill(body);
  await dialog.getByRole("button", { name: "Create inquiry" }).click();
  await expect(page.getByText("Simulated conversation created")).toBeVisible();
  await expect(page.getByText(name).first()).toBeVisible();
}

async function saveAISettings(page: Page) {
  await page.getByRole("button", { name: "Save runtime settings" }).click();
  await expect(page.getByText("AI runtime settings saved")).toBeVisible();
}

async function enableAITool(page: Page, name: string) {
  await page.goto("/en/app/settings/ai-automation");
  const control = page.getByLabel(`Enable ${name}`);
  if (await control.isChecked()) return;
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("/ai/tool-policies/") &&
        response.request().method() === "PATCH",
    ),
    control.click(),
  ]);
  await expect(control).toBeChecked();
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

test("@screenshots AI runtime evidence", async ({ page }) => {
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);

  await page.goto("/en/app/settings/ai-automation");
  await page.getByLabel("Enable AI runtime").check();
  await page.getByLabel("Default mode").selectOption("suggest");
  const internalChannel = page.getByRole("checkbox", {
    name: /Development test channel/,
  });
  if (!(await internalChannel.isChecked())) await internalChannel.check();
  await saveAISettings(page);
  await page.screenshot({
    path: resolve(screenshotDir, "ai-automation-settings.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(screenshotDir, "ai-automation-settings-mobile.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await page.setViewportSize({ width: 1440, height: 1000 });
  await createAIInquiry(
    page,
    "Screenshot AI Draft",
    "Hello, tell me about your services.",
  );
  await expect(page.getByText("Response draft")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-draft-inbox.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page
    .getByLabel("Edit AI draft")
    .fill("This is a human-reviewed edit ready for the internal test channel.");
  await page.screenshot({
    path: resolve(screenshotDir, "ai-edited-draft.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: resolve(screenshotDir, "ai-mobile-draft-workflow.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByRole("button", { name: "Reject", exact: true }).click();

  await enableAITool(page, "create_lead");
  await createAIInquiry(
    page,
    "Screenshot AI Tool",
    "Please create lead for this inquiry",
  );
  await expect(
    page.getByRole("button", { name: "Approve tool" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-tool-approval.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByRole("button", { name: "Approve tool" }).click();

  await createAIInquiry(
    page,
    "Screenshot Handoff",
    "I need a human manager now.",
  );
  await expect(page.getByText("Human handoff required")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-handoff-banner.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await createAIInquiry(page, "Screenshot Failure", "[[fake:provider_error]]");
  await expect(page.getByText("AI run failed safely")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-failed-run.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto("/en/app/settings/ai-automation");
  await page.locator(".ai-run-list details").first().click();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-safe-run-detail.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await createAIInquiry(
    page,
    "Screenshot Russian",
    "Здравствуйте, расскажите об услугах",
  );
  await page.goto("/ru/app/inbox");
  await page.getByRole("button", { name: /Screenshot Russian/ }).click();
  await expect(page.getByText(/Создано AI/)).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-state-ru.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto("/en/app/inbox");
  await page.getByRole("button", { name: /Screenshot Russian/ }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();

  await createAIInquiry(
    page,
    "Screenshot Uzbek",
    "Salom, xizmatlar haqida ayting",
  );
  await page.goto("/uz/app/inbox");
  await page.getByRole("button", { name: /Screenshot Uzbek/ }).click();
  await expect(page.getByText(/AI yaratgan/)).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-state-uz.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto("/en/app/inbox");
  await page.getByRole("button", { name: /Screenshot Uzbek/ }).click();
  await page.getByRole("button", { name: "Reject", exact: true }).click();

  await page.goto("/en/app/settings/ai-automation");
  await page.getByLabel("Default mode").selectOption("autopilot_test");
  await saveAISettings(page);
  await createAIInquiry(
    page,
    "Screenshot Autopilot",
    "Hello from internal autopilot.",
  );
  await expect(page.getByText("AI-generated content")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-test-autopilot-result.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await page.goto("/en/app/settings/ai-automation");
  await page.getByLabel("Default mode").selectOption("suggest");
  await page.getByLabel("Daily run limit").fill("1");
  await saveAISettings(page);
  await createAIInquiry(
    page,
    "Screenshot Usage Limit",
    "Hello after reaching the daily limit.",
  );
  await page.getByRole("button", { name: "Generate draft" }).click();
  await expect(page.getByText("daily_run_limit")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "ai-usage-limit-reached.png"),
    fullPage: true,
    style: screenshotStyle,
  });
});
