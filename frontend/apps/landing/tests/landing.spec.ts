import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
});

const localizedHeadings = {
  ru: /Каждое обращение/,
  uz: /Har bir murojaat/,
  en: /Every inquiry/,
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
  await expect(page.getByText(/AI front office in development/)).toBeVisible();
  await page.getByRole("button", { name: "What is this platform?" }).click();
  await expect(page.getByText(/AI front office in development/)).toBeHidden();
});

test("scenario tabs expose context, permissions, and keyboard navigation", async ({
  page,
}) => {
  await page.goto("/ru");
  const salon = page.getByRole("tab", { name: "Салон красоты" });
  const clinic = page.getByRole("tab", { name: "Клиника" });

  await salon.focus();
  await salon.press("ArrowRight");

  await expect(clinic).toHaveAttribute("aria-selected", "true");
  const panel = page.getByRole("tabpanel");
  await expect(panel.getByText("Расписание врачей")).toBeVisible();
  await expect(
    panel.getByText("Передать администратору", { exact: true }),
  ).toBeVisible();
  await expect(panel.getByText("Сохранено в CRM")).toBeVisible();
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

test("cinematic journey becomes ready and follows every product stage", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/ru");

  const journey = page.locator("[data-cinematic-journey]");
  await expect(journey).toHaveAttribute("data-scene-ready", "true", {
    timeout: 15_000,
  });

  if ((await journey.getAttribute("data-render-path")) === "live") {
    await expect(journey.locator("canvas")).toHaveCount(1);
    await expect(journey).toHaveAttribute(
      "data-model-type",
      "exact-svg-logo-webgl-3d",
    );
    await expect(journey).toHaveAttribute("data-actor-meshes", "1");
    await expect(journey).toHaveAttribute("data-procedural-model", "false");
    await expect(journey).toHaveAttribute(
      "data-orbit-mode",
      "free-360-inertia",
    );
    await expect(journey).toHaveAttribute("data-orbit-ready", "true");
    const triangleCount = Number(await journey.getAttribute("data-triangles"));
    expect(triangleCount).toBeGreaterThan(1_000);
    expect(triangleCount).toBeLessThan(30_000);
  }

  for (const shot of [
    "identity",
    "ready",
    "receive",
    "understand",
    "act",
    "remember",
  ]) {
    await page
      .locator(`[data-shot="${shot}"]`)
      .evaluate((element) => element.scrollIntoView({ block: "center" }));
    await expect(journey).toHaveAttribute("data-active-shot", shot);
  }

  await page
    .locator('[data-shot="ready"]')
    .evaluate((element) => element.scrollIntoView({ block: "center" }));
  await expect(journey).toHaveAttribute("data-active-shot", "ready");
});

test("the exact logo supports a full pointer orbit with inertia", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/ru");

  const journey = page.locator("[data-cinematic-journey]");
  await expect(journey).toHaveAttribute("data-render-path", "live");
  await expect(journey).toHaveAttribute("data-orbit-ready", "true", {
    timeout: 15_000,
  });
  const rotator = page.locator("[data-logo-rotator]");
  await expect(rotator).toHaveCount(1);

  const box = await rotator.boundingBox();
  expect(box).not.toBeNull();
  const y = (box?.y ?? 0) + Math.min(330, (box?.height ?? 720) * 0.46);
  const startX = (box?.x ?? 0) + 100;
  const endX = (box?.x ?? 0) + (box?.width ?? 1280) - 100;
  const yawBefore = Number(await journey.getAttribute("data-orbit-yaw"));

  await page.mouse.move(startX, y);
  await page.mouse.down();
  await expect(journey).toHaveAttribute("data-orbit-dragging", "true");
  await page.mouse.move(endX, y, { steps: 28 });
  await page.waitForTimeout(140);
  const yawAtRelease = Number(await journey.getAttribute("data-orbit-yaw"));
  await page.mouse.up();

  expect(Math.abs(yawAtRelease - yawBefore)).toBeGreaterThan(Math.PI * 2);
  await expect(journey).toHaveAttribute("data-orbit-input", "pointer");
  await expect(journey).toHaveAttribute("data-orbit-inertia", "true");
  await page.waitForTimeout(180);
  const yawAfterInertia = Number(await journey.getAttribute("data-orbit-yaw"));
  expect(Math.abs(yawAfterInertia - yawAtRelease)).toBeGreaterThan(0.015);
  await expect(journey).toHaveAttribute("data-actor-meshes", "1");
});

test("the 3D logo can be inspected and reset from the keyboard", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto("/en");

  const journey = page.locator("[data-cinematic-journey]");
  await expect(journey).toHaveAttribute("data-orbit-ready", "true", {
    timeout: 15_000,
  });
  const rotator = page.locator("[data-logo-rotator]");
  await expect(rotator).toHaveAttribute("role", "application");
  await expect(rotator).toHaveAttribute("aria-describedby");

  await rotator.focus();
  const yawBefore = Number(await journey.getAttribute("data-orbit-yaw"));
  const pitchBefore = Number(await journey.getAttribute("data-orbit-pitch"));
  await rotator.press("ArrowRight");
  await rotator.press("ArrowRight");
  await rotator.press("ArrowUp");
  await page.waitForTimeout(160);

  expect(Number(await journey.getAttribute("data-orbit-yaw"))).toBeGreaterThan(
    yawBefore + 0.25,
  );
  expect(Number(await journey.getAttribute("data-orbit-pitch"))).toBeLessThan(
    pitchBefore - 0.08,
  );
  await expect(journey).toHaveAttribute("data-orbit-input", "keyboard");

  await rotator.press("Home");
  await expect
    .poll(
      async () => {
        const yaw = Number(await journey.getAttribute("data-orbit-yaw"));
        const pitch = Number(await journey.getAttribute("data-orbit-pitch"));
        return Math.max(
          Math.abs(Math.atan2(Math.sin(yaw), Math.cos(yaw))),
          Math.abs(pitch),
        );
      },
      { timeout: 5000 },
    )
    .toBeLessThan(0.02);
});

test("reduced motion receives the designed poster path", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/ru");
  const journey = page.locator("[data-cinematic-journey]");
  await expect(journey).toHaveAttribute("data-render-path", "poster");
  await expect(journey).toHaveAttribute("data-render-reason", "reduced");
  await expect(journey).toHaveAttribute("data-scene-ready", "true");
  await expect(journey).toHaveAttribute("data-orbit-ready", "false");
  await expect(journey.locator("canvas")).toHaveCount(0);
});
