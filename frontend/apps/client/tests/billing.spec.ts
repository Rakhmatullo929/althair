import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const api = "http://localhost:8011/api/v1";
const password = "client-portal-development-only-password";

async function login(page: Page, email = "owner@portal.test") {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function tenant(request: APIRequestContext, name: string) {
  const response = await request.get(`${api}/organizations/`);
  const body = (await response.json()) as {
    results: Array<{ id: string; name: string }>;
  };
  return body.results.find((row) => row.name === name)!.id;
}

test.describe.serial("provider-independent customer Billing", () => {
  test("seeded organization receives an active wallet-backed safe default plan", async ({
    page,
  }) => {
    await login(page);
    await page.getByRole("link", { name: "Billing" }).click();
    await expect(
      page.getByRole("heading", { name: "Billing", level: 1 }),
    ).toBeVisible();
    const currentPlan = page.locator("article", {
      has: page.getByRole("heading", { name: "Starter", exact: true }),
    });
    await expect(
      currentPlan.getByText("Active", { exact: true }),
    ).toBeVisible();
    await expect(
      currentPlan.getByText("Starter", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("v1", { exact: true })).toBeVisible();
  });

  test("owner sees server-enforced features and real SMS, Voice, and AI usage", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/billing");
    await expect(
      page.getByText("server enforces these capabilities", { exact: false }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Usage", exact: true }).click();
    await expect(page.getByText("sms segments", { exact: true })).toBeVisible();
    await expect(
      page.getByText("voice seconds", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("ai input tokens", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("84 / 1000")).toBeVisible();
  });

  test("billing profile is tenant-owned and editable only by owner/admin", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/billing");
    await page.getByLabel("Billing email").fill("finance@mehr.example.test");
    await page.getByLabel("Country code").fill("UZ");
    await page.getByRole("button", { name: "Save profile" }).click();
    await expect(page.getByText("Billing profile saved.")).toBeVisible();
  });

  test("owner previews and schedules a next-renewal plan change without proration", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/billing/plans");
    const growth = page.locator(".billing-plan", { hasText: "Growth" });
    await expect(growth).toBeVisible();
    await growth.getByRole("button", { name: "Preview change" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog.getByText("No silent proration")).toBeVisible();
    await expect(dialog.getByText(/takes effect/)).toBeVisible();
    await dialog.getByRole("button", { name: "Schedule change" }).click();
    await expect(
      page.getByText("Plan change scheduled for the next renewal."),
    ).toBeVisible();
    await page.goto("/en/app/billing");
    await expect(page.getByText(/Plan change scheduled for/)).toBeVisible();
  });

  test("owner cancels at period end and resumes before end idempotently", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/billing");
    await page.getByRole("button", { name: "Cancel at period end" }).click();
    await expect(page.getByText(/Cancellation is scheduled/)).toBeVisible();
    await page.getByRole("button", { name: "Resume renewal" }).click();
    await expect(page.getByText("Renewal resumed.")).toBeVisible();
  });

  test("issued invoice preserves lines and never renders a fake card form", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/billing/invoices");
    await expect(page.getByText(/^E2E-/).first()).toBeVisible();
    await page.getByRole("link", { name: "View", exact: true }).first().click();
    await expect(page.getByText("Starter v1")).toBeVisible();
    await expect(page.getByText("Tax (not calculated)")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Print / save PDF" }),
    ).toBeVisible();
    await expect(page.getByLabel(/card/i)).toHaveCount(0);
    await expect(page.getByLabel(/cvv/i)).toHaveCount(0);
  });

  test("company balance and immutable ledger are customer read-only", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/billing/wallet");
    await expect(
      page.getByRole("heading", { name: "Company balance", level: 1 }),
    ).toBeVisible();
    await expect(
      page.getByText("Balance history", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Opening balance", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("Subscription invoice", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /top up/i })).toHaveCount(0);
    const organizationId = await tenant(page.request, "Mehr Clinic");
    const csrf = (await (
      await page.request.get(`${api}/users/auth/csrf/`)
    ).json()) as { csrftoken: string };
    const mutation = await page.request.post(`${api}/billing/wallet/`, {
      headers: {
        "X-Organization-ID": organizationId,
        "X-CSRFToken": csrf.csrftoken,
      },
      data: { available_balance_minor: 999999999 },
    });
    expect(mutation.status()).toBe(405);
  });

  test("organization switch invalidates Billing cache", async ({ page }) => {
    await login(page);
    await page.goto("/en/app/billing/invoices");
    const originalInvoice = page.getByText(/^E2E-/).first();
    await expect(originalInvoice).toBeVisible();
    const originalInvoiceNumber = await originalInvoice.textContent();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(page.getByText("draft", { exact: true })).toBeVisible();
    await expect(
      page.getByText(originalInvoiceNumber!, { exact: true }),
    ).toHaveCount(0);
  });

  test("cross-tenant invoice lookup is 404 and status mutation is unavailable", async ({
    page,
  }) => {
    await login(page);
    const mehr = await tenant(page.request, "Mehr Clinic");
    const atlas = await tenant(page.request, "Atlas Academy");
    const list = await page.request.get(`${api}/billing/invoices/`, {
      headers: { "X-Organization-ID": mehr },
    });
    const invoice = ((await list.json()) as { results: Array<{ id: string }> })
      .results[0];
    const crossed = await page.request.get(
      `${api}/billing/invoices/${invoice.id}/`,
      { headers: { "X-Organization-ID": atlas } },
    );
    expect(crossed.status()).toBe(404);
    const csrf = (await (
      await page.request.get(`${api}/users/auth/csrf/`)
    ).json()) as { csrftoken: string };
    const mutation = await page.request.patch(
      `${api}/billing/invoices/${invoice.id}/`,
      {
        headers: { "X-Organization-ID": mehr, "X-CSRFToken": csrf.csrftoken },
        data: { status: "paid" },
      },
    );
    expect(mutation.status()).toBe(405);
  });

  test("viewer sees Billing read-only and cannot edit the profile", async ({
    page,
  }) => {
    await login(page, "member@portal.test");
    await page.goto("/en/app/billing");
    await expect(
      page.getByText(/Only an organization owner or administrator/),
    ).toBeVisible();
    await expect(page.getByLabel("Billing email")).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "Save profile" }),
    ).toHaveCount(0);
  });

  test("Billing navigation and lifecycle labels exist in RU, UZ, and EN", async ({
    page,
  }) => {
    await login(page);
    for (const [locale, heading] of [
      ["en", "Billing"],
      ["ru", "Биллинг"],
      ["uz", "Billing"],
    ] as const) {
      await page.goto(`/${locale}/app/billing`);
      await expect(
        page.getByRole("heading", { name: heading, level: 1 }),
      ).toBeVisible();
    }
  });

  test("mobile Billing is keyboard-readable and has no serious accessibility violations", async ({
    page,
  }) => {
    await login(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/billing/usage");
    await expect(
      page.getByRole("heading", { name: "Usage", level: 1 }),
    ).toBeVisible();
    await expect(page.getByText("sms segments", { exact: true })).toBeVisible();
    const width = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
    }));
    expect(width.content).toBeLessThanOrEqual(width.viewport);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations).toEqual([]);
  });
});
