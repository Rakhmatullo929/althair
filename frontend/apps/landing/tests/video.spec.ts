import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const videoPath = "/videos/althair-client-demo-0820-v1.mp4";
const posterPath = "/videos/althair-client-demo-0820-v1-poster.webp";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
});

test("@video serves the exact public route without a locale redirect", async ({
  request,
}) => {
  const response = await request.get("/video", { maxRedirects: 0 });

  expect(response.status()).toBe(200);
  expect(response.url()).toMatch(/\/video$/);
  expect(response.headers().location).toBeUndefined();
});

test("@video exposes an accessible native player with safe defaults", async ({
  page,
}) => {
  const consoleErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("/video");

  await expect(
    page.getByRole("heading", {
      level: 1,
      name: "Althair AI — демонстрация платформы",
    }),
  ).toBeVisible();

  const video = page.locator("video");
  await expect(video).toHaveAttribute("controls", "");
  await expect(video).toHaveAttribute("playsinline", "");
  await expect(video).toHaveAttribute("preload", "metadata");
  await expect(video).toHaveAttribute("poster", posterPath);
  await expect(video).not.toHaveAttribute("autoplay", "");
  await expect(video.locator("source")).toHaveAttribute("src", videoPath);
  await expect(video.locator("source")).toHaveAttribute("type", "video/mp4");

  await expect
    .poll(() =>
      video.evaluate((element) => (element as HTMLVideoElement).readyState),
    )
    .toBeGreaterThanOrEqual(1);
  expect(
    await video.evaluate((element) => (element as HTMLVideoElement).duration),
  ).toBeGreaterThan(177);
  expect(consoleErrors).toEqual([]);
});

test("@video starts real MP4 playback after an explicit action", async ({
  page,
}) => {
  await page.goto("/video");
  const video = page.locator("video");

  await video.evaluate(async (element) => {
    const player = element as HTMLVideoElement;
    player.muted = true;
    await player.play();
  });

  await expect
    .poll(() =>
      video.evaluate((element) => (element as HTMLVideoElement).currentTime),
    )
    .toBeGreaterThan(0);
  await video.evaluate((element) => (element as HTMLVideoElement).pause());
});

test("@video remains responsive at an iPhone-like viewport", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/video");
  await expect(page.locator("video")).toBeVisible();

  const dimensions = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(dimensions.scroll).toBeLessThanOrEqual(dimensions.client + 1);
});

test("@video shows an honest recovery state when media fails", async ({
  page,
}) => {
  await page.route(`**${videoPath}`, (route) => route.abort("failed"));
  await page.goto("/video");
  await page.locator("video").dispatchEvent("error");

  const alert = page
    .locator('[role="alert"]')
    .filter({ hasText: "Видео сейчас не удалось загрузить." });
  await expect(alert).toContainText("Видео сейчас не удалось загрузить.");
  await expect(alert.getByRole("button", { name: "Повторить" })).toBeVisible();
  await expect(
    alert.getByRole("link", { name: "Открыть видео отдельно" }),
  ).toHaveAttribute("href", videoPath);
});

test("@video has no serious accessibility issues or private media URL", async ({
  page,
}) => {
  await page.goto("/video");
  const html = await page.locator("html").innerHTML();

  expect(html).not.toMatch(
    /(?:amazonaws\.com|blob\.vercel-storage\.com|X-Amz-|token=|localhost:\d+\/videos)/i,
  );

  const results = await new AxeBuilder({ page }).analyze();
  expect(
    results.violations.filter((item) =>
      ["serious", "critical"].includes(item.impact ?? ""),
    ),
  ).toEqual([]);
});
