import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const api = "http://localhost:8012/api/v1";
const internal = `${api}/internal`;
const password = "internal-platform-development-only";

async function csrf(request: APIRequestContext) {
  const response = await request.get(`${internal}/auth/csrf/`);
  return String(((await response.json()) as { csrftoken: string }).csrftoken);
}

async function loginInternal(
  page: Page,
  email = "platform-owner@example.test",
) {
  const token = await csrf(page.request);
  expect(
    (
      await page.request.post(`${internal}/auth/login/`, {
        headers: { "X-CSRFToken": token },
        data: { email, password },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await page.request.post(`${internal}/auth/mfa/verify/`, {
        headers: { "X-CSRFToken": token },
        data: { code: "000000" },
      })
    ).status(),
  ).toBe(200);
  return token;
}

test.describe.serial("Internal Super Admin Billing", () => {
  test("platform owner inspects plans with safe provider-independent data", async ({
    page,
  }) => {
    await loginInternal(page);
    await page.goto("/en/app/billing/plans");
    await expect(
      page.getByRole("heading", { name: "Plan catalog", level: 1 }),
    ).toBeVisible();
    await expect(page.getByText("Starter").first()).toBeVisible();
    await expect(
      page.getByText(/No card or payment credentials/),
    ).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });

  test("platform owner creates and publishes an immutable plan version", async ({
    page,
  }) => {
    await loginInternal(page);
    await page.goto("/en/app/billing/plans");
    await page.getByRole("button", { name: "Create plan version" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Plan key").fill("e2e-scale");
    await dialog.getByLabel("Display name").fill("E2E Scale");
    await dialog
      .getByLabel("Description")
      .fill("Synthetic plan publication evidence");
    await dialog.getByLabel("Amount in minor units").fill("39900000");
    await dialog
      .getByLabel("Review reason")
      .fill("Synthetic E2E plan catalogue review");
    await dialog.getByRole("button", { name: "Create draft" }).click();
    await expect(
      page.getByText("Draft plan version created and audited."),
    ).toBeVisible();
    const card = page.locator(".billing-admin-card", { hasText: "E2E Scale" });
    await card.getByRole("button", { name: "Publish" }).click();
    const publish = page.getByRole("dialog");
    await publish
      .getByLabel("Review reason")
      .fill("Synthetic E2E publication approval");
    await publish
      .getByRole("button", { name: "Confirm reviewed action" })
      .click();
    await expect(
      page.getByText("Reviewed Billing action completed and audited."),
    ).toBeVisible();
    await expect(
      card.getByText("active", { exact: true }).first(),
    ).toBeVisible();
  });

  test("subscription detail includes entitlement snapshot and grace can be reviewed", async ({
    page,
  }) => {
    const token = await loginInternal(page);
    const list = (await (
      await page.request.get(`${internal}/billing/subscriptions/`)
    ).json()) as { results: Array<{ id: string }> };
    const detail = await page.request.get(
      `${internal}/billing/subscriptions/${list.results[0].id}/`,
    );
    expect(JSON.stringify(await detail.json())).toContain("entitlements");
    await page.goto("/en/app/billing/subscriptions");
    const first = page.locator(".billing-admin-card").first();
    await first.getByRole("button", { name: "Extend grace" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByLabel("Days").fill("3");
    await dialog
      .getByLabel("Review reason")
      .fill("Synthetic grace extension approval");
    await dialog
      .getByRole("button", { name: "Confirm reviewed action" })
      .click();
    await expect(
      page.getByText("Reviewed Billing action completed and audited."),
    ).toBeVisible();
    expect(token).toBeTruthy();
  });

  test("support can inspect but cannot record a manual payment", async ({
    page,
  }) => {
    const token = await loginInternal(page, "platform-support@example.test");
    const invoices = (await (
      await page.request.get(`${internal}/billing/invoices/?status=open`)
    ).json()) as { results: Array<{ id: string }> };
    const response = await page.request.post(
      `${internal}/billing/invoices/${invoices.results[0].id}/mark-paid/`,
      {
        headers: { "X-CSRFToken": token },
        data: { reason: "Support must remain read only" },
      },
    );
    expect(response.status()).toBe(403);
    await page.goto("/en/app/billing/invoices");
    await expect(
      page.getByRole("button", { name: "Reviewed mark paid" }),
    ).toHaveCount(0);
  });

  test("platform owner records reviewed fake/manual payment with MFA and audit", async ({
    page,
  }) => {
    await loginInternal(page);
    await page.goto("/en/app/billing/invoices");
    const open = page
      .locator(".billing-admin-card", { hasText: "open" })
      .first();
    const invoiceNumber = await open.locator("strong").first().textContent();
    await open.getByRole("button", { name: "Reviewed mark paid" }).click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByLabel("Review reason")
      .fill("Synthetic finance review completed");
    await dialog
      .getByRole("button", { name: "Confirm reviewed action" })
      .click();
    await expect(
      page.getByText("Reviewed Billing action completed and audited."),
    ).toBeVisible();
    const paid = page.locator(".billing-admin-card", {
      hasText: invoiceNumber!,
    });
    await expect(paid.getByText("paid", { exact: true }).first()).toBeVisible();
  });

  test("draft invoice can be issued and then voided without rewriting history", async ({
    page,
  }) => {
    await loginInternal(page);
    await page.goto("/en/app/billing/invoices");
    let card = page
      .locator(".billing-admin-card", { hasText: "draft" })
      .first();
    await card.getByRole("button", { name: "Issue" }).click();
    await page
      .getByRole("dialog")
      .getByLabel("Review reason")
      .fill("Synthetic invoice issue approval");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm reviewed action" })
      .click();
    card = page.locator(".billing-admin-card", { hasText: "open" }).first();
    await card.getByRole("button", { name: "Void" }).click();
    await page
      .getByRole("dialog")
      .getByLabel("Review reason")
      .fill("Synthetic invoice void approval");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Confirm reviewed action" })
      .click();
    await expect(
      page.locator(".billing-admin-card", { hasText: "void" }).first(),
    ).toBeVisible();
  });

  test("operations reconciliation is idempotent and provider failures are inspectable", async ({
    page,
  }) => {
    const token = await loginInternal(page, "platform-operations@example.test");
    const subscriptions = (await (
      await page.request.get(`${internal}/billing/subscriptions/`)
    ).json()) as { results: Array<{ organization: string }> };
    const data = {
      organization_id: subscriptions.results[0].organization,
      reason: "Synthetic usage reconciliation review",
    };
    const first = await page.request.post(
      `${internal}/billing/usage/reconcile/`,
      { headers: { "X-CSRFToken": token }, data },
    );
    const second = await page.request.post(
      `${internal}/billing/usage/reconcile/`,
      { headers: { "X-CSRFToken": token }, data },
    );
    expect(first.status()).toBe(200);
    expect(await first.text()).toBe(await second.text());
    await page.goto("/en/app/billing/usage");
    await expect(page.getByText("Verified provider events")).toBeVisible();
    await expect(
      page.getByText("failed", { exact: true }).first(),
    ).toBeVisible();
  });

  test("internal Billing works in EN/RU and on mobile without overflow", async ({
    page,
  }) => {
    await loginInternal(page);
    await page.goto("/ru/app/billing/plans");
    await expect(
      page.getByRole("heading", { name: "Каталог тарифов", level: 1 }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/billing/usage");
    await expect(
      page.getByRole("heading", { name: "Usage & events", level: 1 }),
    ).toBeVisible();
    const width = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
    }));
    expect(width.content).toBeLessThanOrEqual(width.viewport);
  });
});
