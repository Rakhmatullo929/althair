import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const ownerEmail = "owner@portal.test";
const agentEmail = "member@portal.test";
const password = "client-portal-development-only-password";
const customerName = "E2E CRM Customer";

async function login(page: Page, email = ownerEmail) {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function openCustomerConversation(page: Page) {
  await page.goto("/en/app/inbox");
  await page.getByRole("button", { name: new RegExp(customerName) }).click();
  await expect(
    page
      .getByLabel("Active conversation")
      .getByRole("heading", { name: customerName }),
  ).toBeVisible();
}

test.describe.serial("tenant CRM workflow", () => {
  test("owner creates a simulated inquiry and an agent claims, replies, notes, resolves, and reopens it", async ({
    page,
  }) => {
    await login(page);
    await page.getByRole("link", { name: "Inbox", exact: true }).click();
    await page.getByRole("button", { name: "Test conversation" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Display name").fill(customerName);
    await dialog
      .getByLabel("Inbound message")
      .fill("I need help choosing the right clinic service.");
    await dialog.getByRole("button", { name: "Create inquiry" }).click();
    await expect(
      page.getByText("Simulated conversation created"),
    ).toBeVisible();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByRole("heading", { name: customerName }),
    ).toBeVisible();
    await expect(
      page.getByText("Unassigned", { exact: true }).first(),
    ).toBeVisible();

    await page.locator(".user-menu summary").click();
    await page.getByRole("button", { name: "Log out" }).click();
    await login(page, agentEmail);
    await openCustomerConversation(page);
    await page.getByLabel("Assignment").selectOption({ label: "Timur Saidov" });
    await expect(
      page.getByLabel("Active conversation").getByText("Conversation assigned"),
    ).toBeVisible();
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("A team member is handling your request.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("A team member is handling your request."),
    ).toBeVisible();
    await page.getByRole("tab", { name: "Internal note" }).click();
    await page
      .getByPlaceholder("Write a note visible only to the team…")
      .fill("Customer prefers a call after 16:00.");
    await page.getByRole("button", { name: "Add note" }).click();
    await expect(
      page
        .getByLabel("Active conversation")
        .getByText("Customer prefers a call after 16:00."),
    ).toBeVisible();
    const markRead = page.getByRole("button", { name: "Mark read" });
    if (await markRead.isVisible()) await markRead.click();
    await page.getByRole("button", { name: "Resolve" }).click();
    await expect(page.getByRole("button", { name: "Reopen" })).toBeVisible();
    await page.getByRole("button", { name: "Reopen" }).click();
    await expect(page.getByRole("button", { name: "Resolve" })).toBeVisible();
  });

  test("contact identity can be created and edited", async ({ page }) => {
    await login(page);
    await page.getByRole("link", { name: "Contacts", exact: true }).click();
    await page.getByRole("button", { name: new RegExp(customerName) }).click();
    await expect(
      page.getByRole("heading", { name: customerName }),
    ).toBeVisible();
    await page.getByLabel("Identity type").selectOption("email");
    await page
      .getByLabel("Phone, email, or external value")
      .fill("e2e.crm@example.test");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(
      page
        .locator(".contact-detail-panel")
        .locator("strong")
        .filter({ hasText: "e2e.crm@example.test" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Edit identity e2e.crm@example.test" })
      .click();
    await page
      .getByLabel("Phone, email, or external value")
      .fill("updated.crm@example.test");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(
      page
        .locator(".contact-detail-panel")
        .locator("strong")
        .filter({ hasText: "updated.crm@example.test" }),
    ).toBeVisible();
  });

  test("conversation becomes a lead and moves through pipeline stages", async ({
    page,
  }) => {
    await login(page);
    await openCustomerConversation(page);
    await page.getByRole("button", { name: "Create lead" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    await page.getByRole("link", { name: "Leads", exact: true }).click();
    const leadTitle = `Inquiry from ${customerName}`;
    await expect(
      page.getByRole("button", { name: new RegExp(leadTitle) }).first(),
    ).toBeVisible();
    await page
      .getByLabel(`Move ${leadTitle} to another stage`)
      .selectOption({ label: "Qualified" });
    await expect(page.getByText("Changes saved")).toBeVisible();
    await expect(page.getByText(leadTitle).first()).toBeVisible();
  });

  test("follow-up task is created from Inbox and completed", async ({
    page,
  }) => {
    await login(page);
    await openCustomerConversation(page);
    await page.getByRole("button", { name: "Create follow-up" }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    await page.getByRole("link", { name: "Tasks", exact: true }).click();
    const taskTitle = `Follow up with ${customerName}`;
    await expect(page.getByText(taskTitle)).toBeVisible();
    await page.getByRole("button", { name: `Complete ${taskTitle}` }).click();
    await expect(page.getByText("Changes saved")).toBeVisible();
    await expect(
      page.locator(".task-completed").getByText(taskTitle),
    ).toBeVisible();
  });

  test("organization switching clears conversations, contacts, and leads", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/inbox");
    await expect(page.getByText(customerName)).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(page.getByText("Inbox is clear")).toBeVisible();
    await expect(page.getByText(customerName)).toHaveCount(0);
    await page.getByRole("link", { name: "Contacts", exact: true }).click();
    await expect(page.getByText(customerName)).toHaveCount(0);
    await page.getByRole("link", { name: "Leads", exact: true }).click();
    await expect(page.getByText(customerName)).toHaveCount(0);
  });

  test("agent cannot merge contacts or manage leads and pipelines", async ({
    page,
  }) => {
    await login(page, agentEmail);
    await page.getByRole("link", { name: "Contacts", exact: true }).click();
    await expect(page.getByRole("button", { name: "Merge" })).toHaveCount(0);
    await page.getByRole("link", { name: "Leads", exact: true }).click();
    await expect(page.getByRole("button", { name: "New lead" })).toHaveCount(0);
    await expect(page.locator(".lead-card select").first()).toBeDisabled();
  });

  test("suspended organization keeps every CRM screen read-only", async ({
    page,
  }) => {
    await login(page);
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Paused Studio · Owner" });
    await expect(page.getByText(/organization is suspended/i)).toBeVisible();
    await page.getByRole("link", { name: "Inbox", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Test conversation" }),
    ).toHaveCount(0);
    await page.getByRole("link", { name: "Contacts", exact: true }).click();
    await expect(page.getByRole("button", { name: "New contact" })).toHaveCount(
      0,
    );
    await page.getByRole("link", { name: "Tasks", exact: true }).click();
    await expect(page.getByRole("button", { name: "New task" })).toHaveCount(0);
  });

  test("RU, UZ, EN Inbox translations and accessibility are complete", async ({
    page,
  }) => {
    await login(page);
    for (const [locale, heading] of [
      ["ru", "Единые входящие"],
      ["uz", "Yagona kiruvchi xabarlar"],
      ["en", "Unified Inbox"],
    ] as const) {
      await page.goto(`/${locale}/app/inbox`);
      await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    }
    const accessibility = await new AxeBuilder({ page }).analyze();
    expect(accessibility.violations).toEqual([]);
  });

  test("desktop three-area Inbox and mobile drill-down avoid overflow", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/inbox");
    await expect(page.locator(".conversation-list-panel")).toBeVisible();
    await expect(page.locator(".conversation-panel")).toBeVisible();
    await expect(page.locator(".contact-context-panel")).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/inbox");
    await expect(page.locator(".conversation-list-panel")).toBeVisible();
    await page.getByRole("button", { name: new RegExp(customerName) }).click();
    await expect(page.getByRole("button", { name: "Back" })).toBeVisible();
    await expect(page.locator(".conversation-list-panel")).toBeHidden();
    await page.getByRole("button", { name: "Back" }).click();
    await expect(page.locator(".conversation-list-panel")).toBeVisible();
    const viewport = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(viewport.scrollWidth, JSON.stringify(viewport)).toBeLessThanOrEqual(
      viewport.clientWidth,
    );
  });
});
