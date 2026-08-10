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

test("@screenshots public Web Chat evidence", async ({ page }) => {
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/en/demo");
  await expect(
    page.getByRole("button", { name: "Start conversation" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "web-chat-demo-page.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.screenshot({
    path: resolve(screenshotDir, "web-chat-live-preview.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-consent.png"),
    style: screenshotStyle,
  });

  await page.evaluate(() => {
    const directWidget = document.querySelector<HTMLElement>(
      ".webchat-demo-frame",
    );
    if (directWidget) directWidget.hidden = true;
    const loader = document.createElement("script");
    loader.src = "/widget.js?v=1";
    loader.dataset.installationKey = "wc_demo_portal_test";
    loader.dataset.apiUrl = "http://localhost:8011/api/v1";
    loader.dataset.appUrl = "http://localhost:3001";
    loader.dataset.locale = "en";
    document.body.appendChild(loader);
  });
  const launcher = page.getByRole("button", { name: "Open Web Chat" });
  await expect(launcher).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "web-chat-widget-collapsed.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await launcher.click();
  await expect(page.locator('iframe[title="Web Chat"]')).toBeVisible();
  await expect(
    page
      .frameLocator('iframe[title="Web Chat"]')
      .getByRole("button", { name: "Start conversation" }),
  ).toBeVisible({ timeout: 10_000 });
  await page.screenshot({
    path: resolve(screenshotDir, "web-chat-widget-open.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await page.goto("/en/demo");
  await page.getByLabel("Name").fill("Screenshot Widget Visitor");
  await page.getByLabel("Email").fill("screenshot-widget@example.test");
  await page.locator(".webchat-consent input").check();
  await page.getByRole("button", { name: "Start conversation" }).click();
  await expect(page.locator(".webchat-composer")).toBeVisible();
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-active-conversation.png"),
    style: screenshotStyle,
  });
  await page
    .getByPlaceholder("Write a message…")
    .fill("Hello, show me the published information.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/I received your question/i)).toBeVisible({
    timeout: 15_000,
  });
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-ai-reply.png"),
    style: screenshotStyle,
  });
  await page.getByRole("button", { name: "Talk to a person" }).click();
  await expect(
    page.getByText("A team member will continue here."),
  ).toBeVisible();
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-human-handoff.png"),
    style: screenshotStyle,
  });

  await page.context().setOffline(true);
  await expect(page.getByText("Connection lost. Retrying…")).toBeVisible({
    timeout: 7_000,
  });
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-offline.png"),
    style: screenshotStyle,
  });
  await page.context().setOffline(false);
  await expect(page.getByText("Connection lost. Retrying…")).toHaveCount(0, {
    timeout: 7_000,
  });

  await page.route("**/public/web-chat/sessions/*/messages/", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({ error: { code: "rate_limited" } }),
    });
  });
  await page.getByPlaceholder("Write a message…").fill("One message too many");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".webchat-error")).toContainText("rate_limited");
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-rate-limited.png"),
    style: screenshotStyle,
  });
  await page.unroute("**/public/web-chat/sessions/*/messages/");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".webchat-widget").screenshot({
    path: resolve(screenshotDir, "web-chat-mobile-fullscreen.png"),
    style: screenshotStyle,
  });

  for (const [locale, name] of [
    ["ru", "web-chat-ru.png"],
    ["uz", "web-chat-uz.png"],
  ] as const) {
    await page.goto(`/${locale}/demo`);
    await page.evaluate(() => sessionStorage.clear());
    await page.reload();
    await expect(page.locator(".webchat-welcome")).toBeVisible();
    await page.locator(".webchat-widget").screenshot({
      path: resolve(screenshotDir, name),
      style: screenshotStyle,
    });
  }

  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /Screenshot Widget Visitor/ })
    .first()
    .click();
  await expect(page.getByText(/I received your question/i)).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "web-chat-unified-inbox.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await page.goto("/en/app/settings/channels/web-chat");
  await expect(
    page.getByRole("heading", { name: "Public Web Chat" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "web-chat-client-setup.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.getByRole("link", { name: "Configure" }).first().click();
  await expect(
    page.getByRole("heading", { name: "Visitor experience" }),
  ).toBeVisible();
  await page.locator(".webchat-status-strip").screenshot({
    path: resolve(screenshotDir, "web-chat-installation-health.png"),
    style: screenshotStyle,
  });
  const embedPanel = page.locator("section.panel").filter({
    has: page.getByRole("heading", { name: "Iframe loader code" }),
  });
  await embedPanel.screenshot({
    path: resolve(screenshotDir, "web-chat-embed-code.png"),
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
