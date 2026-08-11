import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const ownerEmail = "owner@portal.test";
const agentEmail = "member@portal.test";
const password = "client-portal-development-only-password";

async function login(page: Page, email = ownerEmail) {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function createInquiry(page: Page, name: string, body: string) {
  await page.goto("/en/app/inbox");
  await page.getByRole("button", { name: "Test conversation" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Display name").fill(name);
  await dialog.getByLabel("Inbound message").fill(body);
  await dialog.getByRole("button", { name: "Create inquiry" }).click();
  await expect(page.getByText("Simulated conversation created")).toBeVisible();
  await expect(
    page.getByLabel("Active conversation").getByRole("heading", { name }),
  ).toBeVisible();
}

async function enableTool(page: Page, tool: string) {
  await page.goto("/en/app/settings/ai-automation");
  const control = page.getByLabel(`Enable ${tool}`);
  if (!(await control.isChecked())) {
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/ai/tool-policies/") &&
          response.request().method() === "PATCH",
      ),
      control.click(),
    ]);
    await expect(page.getByText("Tool policy saved")).toBeVisible();
    await expect(control).toBeChecked();
  }
}

test.describe.serial("safe AI conversation runtime", () => {
  test("owner enables suggest mode only for the internal test channel", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/ai-automation");
    await expect(
      page.getByRole("heading", { name: "AI Automation" }),
    ).toBeVisible();
    await expect(
      page.getByText("Allowed test, Instagram, and Telegram channels"),
    ).toBeVisible();
    await page.getByLabel("Enable AI runtime").check();
    await page.getByLabel("Default mode").selectOption("suggest");
    const internalChannel = page.getByRole("checkbox", {
      name: /Development test channel/,
    });
    if (!(await internalChannel.isChecked())) await internalChannel.check();
    await page.getByRole("button", { name: "Save runtime settings" }).click();
    await expect(page.getByText("AI runtime settings saved")).toBeVisible();
    await expect(page.getByText("Deterministic fake ready")).toBeVisible();
    await expect(page.getByText("Server-side only")).toBeVisible();
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test("inbound message creates a draft that can be approved unchanged", async ({
    page,
  }) => {
    await login(page);
    await createInquiry(
      page,
      "AI Approve Customer",
      "Hello, which services are available?",
    );
    await expect(page.getByText("Response draft")).toBeVisible();
    await expect(page.getByText(/AI generated · EN/)).toBeVisible();
    await page.getByRole("button", { name: "Approve and send" }).click();
    await expect(page.getByText("AI-generated content")).toBeVisible();
    await expect(page.getByText("Response draft")).toHaveCount(0);
  });

  test("drafts can be edited and sent or rejected without a message", async ({
    page,
  }) => {
    await login(page);
    await createInquiry(
      page,
      "AI Edit Customer",
      "Please explain your services in English.",
    );
    await expect(page.getByText("Response draft")).toBeVisible();
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    await page
      .getByLabel("Edit AI draft")
      .fill("An approved, edited response from our team.");
    await page.getByRole("button", { name: "Send edited" }).click();
    await expect(
      page.getByText("An approved, edited response from our team."),
    ).toBeVisible();

    await createInquiry(
      page,
      "AI Reject Customer",
      "Hello, I have another question.",
    );
    await expect(page.getByText("Response draft")).toBeVisible();
    await page.getByRole("button", { name: "Reject", exact: true }).click();
    await expect(page.getByText("Response draft")).toHaveCount(0);
    await expect(page.getByText("AI conversation state updated")).toBeVisible();
  });

  test("mutating lead and task tools wait for approval and write real CRM data", async ({
    page,
  }) => {
    await login(page);
    await enableTool(page, "create_lead");
    await enableTool(page, "create_follow_up_task");

    await createInquiry(
      page,
      "AI Lead Customer",
      "Please create lead for this inquiry",
    );
    await expect(page.getByText("create_lead", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Approve tool" }).click();
    await expect(page.getByText(/"created":true/)).toBeVisible();
    await page.goto("/en/app/leads");
    await expect(page.getByText("AI-qualified inquiry").first()).toBeVisible();

    await createInquiry(
      page,
      "AI Task Customer",
      "Please remind me to follow up tomorrow",
    );
    await expect(
      page.getByText("create_follow_up_task", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Approve tool" }).click();
    await page.goto("/en/app/tasks");
    await expect(
      page.getByText("Follow up on AI conversation").first(),
    ).toBeVisible();
  });

  test("a disabled tool and an explicit customer request cause safe handoff", async ({
    page,
  }) => {
    await login(page);
    await createInquiry(
      page,
      "AI Disabled Tool",
      "Please change my name to Safe Customer",
    );
    await expect(page.getByText("Human handoff required")).toBeVisible();
    await expect(
      page.getByText("tool_disabled", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Acknowledge" }).click();
    await page.getByRole("button", { name: "Resolve and resume" }).click();
    await expect(
      page.getByRole("button", { name: "Generate draft" }),
    ).toBeVisible();

    await createInquiry(
      page,
      "AI Human Customer",
      "I want a human manager, please.",
    );
    await expect(page.getByText("Human handoff required")).toBeVisible();
    await expect(
      page.getByText("customer_request", { exact: true }),
    ).toBeVisible();
  });

  test("human reply pauses AI and removes the now-stale pending draft", async ({
    page,
  }) => {
    await login(page);
    await createInquiry(
      page,
      "AI Human Takeover",
      "Hello, help me with available services.",
    );
    await expect(page.getByText("Response draft")).toBeVisible();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("A human is handling this now.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(page.getByText("AI paused after a human reply")).toBeVisible();
    await expect(page.getByText("Response draft")).toHaveCount(0);
  });

  test("RU, UZ, EN, and mixed-language drafts use supported deterministic behavior", async ({
    page,
  }) => {
    await login(page);
    for (const [index, body, language] of [
      ["ru", "Здравствуйте, расскажите об услугах", "RU"],
      ["uz", "Salom, xizmatlar haqida ayting", "UZ"],
      ["en", "Hello, tell me about services", "EN"],
      ["mixed", "Salom, please tell me about services", "UZ"],
    ] as const) {
      await createInquiry(page, `AI Language ${index}`, body);
      await expect(page.getByText(`AI generated · ${language}`)).toBeVisible();
      await page.getByRole("button", { name: "Reject", exact: true }).click();
    }
    await page.goto("/ru/app/inbox");
    await page.getByRole("button", { name: /AI Language mixed/ }).click();
    await expect(page.getByText("AI-ассистент").first()).toBeVisible();
    await page.goto("/uz/app/inbox");
    await page.getByRole("button", { name: /AI Language mixed/ }).click();
    await expect(page.getByText("AI yordamchi").first()).toBeVisible();
  });

  test("internal autopilot sends locally while external providers remain unavailable", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/ai-automation");
    await page.getByLabel("Default mode").selectOption("autopilot_test");
    await page.getByRole("button", { name: "Save runtime settings" }).click();
    await expect(page.getByText("AI runtime settings saved")).toBeVisible();
    await createInquiry(
      page,
      "AI Autopilot Customer",
      "Hello from the internal test channel",
    );
    await expect(page.getByText("AI-generated content")).toBeVisible();
    await expect(page.getByText("Response draft")).toHaveCount(0);
    await page.goto("/en/app/settings/ai-automation");
    await expect(
      page.getByText(/Other providers remain unavailable/),
    ).toBeVisible();
  });

  test("organization switching, lower roles, suspension, and mobile keep AI isolated", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/ai-automation");
    await expect(page.locator(".ai-run-list details").first()).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(
      page.getByText("No AI runs are stored for this organization yet."),
    ).toBeVisible();
    await expect(page.locator(".ai-run-list details")).toHaveCount(0);

    await page
      .getByLabel("Organization")
      .selectOption({ label: "Paused Studio · Owner" });
    await expect(page.getByText(/organization is suspended/i)).toBeVisible();
    await expect(page.getByLabel("Enable AI runtime")).toBeDisabled();

    await page.locator(".user-menu summary").click();
    await page.getByRole("button", { name: "Log out" }).click();
    await login(page, agentEmail);
    await page.goto("/en/app/settings/ai-automation");
    await expect(page.getByLabel("Enable AI runtime")).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Save runtime settings" }),
    ).toHaveCount(0);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/inbox");
    await page.locator(".conversation-list > li > button").first().click();
    await expect(
      page.getByLabel("Conversation AI controls and drafts"),
    ).toBeVisible();
    const viewport = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(viewport.scrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(
      viewport.clientWidth,
    );
  });
});
