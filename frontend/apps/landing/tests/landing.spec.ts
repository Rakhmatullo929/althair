import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const localizedHeadings = {
  ru: /Каждый диалог/,
  uz: /Har bir suhbat/,
  en: /Every conversation/,
};

for (const [locale, heading] of Object.entries(localizedHeadings)) {
  test(`${locale} landing renders without horizontal overflow`, async ({
    page,
  }) => {
    await page.goto(`/${locale}`);
    await expect(
      page.getByRole("heading", { level: 1, name: heading }),
    ).toBeVisible();
    const dimensions = await page.evaluate(() => ({
      client: document.documentElement.clientWidth,
      scroll: document.documentElement.scrollWidth,
    }));
    expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
  });
}

test("desktop navigation, anchor, and locale switch work", async ({ page }) => {
  await page.goto("/ru");
  await page
    .getByRole("navigation", { name: "Основная навигация" })
    .getByRole("link", { name: "Каналы" })
    .click();
  await expect(page).toHaveURL(/#channels$/);

  await page.locator("header select").selectOption("en");
  await expect(page).toHaveURL(/\/en#channels$/);
  await expect(
    page.getByRole("heading", { name: "Common questions" }),
  ).toBeVisible();
});

test("FAQ accordion reveals its answer", async ({ page }) => {
  await page.goto("/en#faq");
  const trigger = page.getByRole("button", {
    name: "What is this platform?",
  });
  await trigger.click();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText(/future unified AI platform/)).toBeVisible();
  await page.getByRole("button", { name: "What is this platform?" }).click();
  await expect(page.getByText(/future unified AI platform/)).toBeHidden();
});

test("mobile navigation opens, follows an anchor, and closes", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/ru");
  await page.getByRole("button", { name: "Открыть меню" }).click();
  const mobileNav = page.getByRole("navigation", {
    name: "Мобильная навигация",
  });
  await expect(mobileNav).toBeVisible();
  await mobileNav.getByRole("link", { name: "Отрасли" }).click();
  await expect(page).toHaveURL(/#industries$/);
  await expect(mobileNav).toBeHidden();
  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
});

test("early-access form announces validation errors", async ({ page }) => {
  await page.goto("/en");
  await page.getByTestId("early-access-trigger").first().click();
  await expect(
    page.getByRole("dialog", { name: "Early-access request" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Send request" }).click();
  await expect(
    page.getByText("Enter a valid email address or phone number"),
  ).toBeVisible();
  await expect(page.getByText("Consent is required")).toBeVisible();
});

test("missing lead webhook returns an honest demo-mode response", async ({
  request,
}) => {
  const response = await request.post("/api/early-access", {
    data: {
      fullName: "Demo User",
      companyName: "Demo Company",
      contact: "demo@example.com",
      industry: "Other",
      preferredChannel: "Email",
      note: "",
      consent: true,
      website: "",
      startedAt: Date.now() - 5000,
      locale: "en",
    },
  });
  expect(response.status()).toBe(503);
  await expect(response.json()).resolves.toMatchObject({
    ok: false,
    code: "DEMO_MODE",
  });
});

test("landing has no serious automated accessibility violations", async ({
  page,
}) => {
  await page.goto("/ru");
  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
});
