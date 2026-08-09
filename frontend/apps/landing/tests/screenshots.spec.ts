import { expect, test } from "@playwright/test";
import path from "node:path";

const screenshotDirectory = path.resolve(
  process.cwd(),
  "../../artifacts/screenshots",
);

const captures = [
  {
    name: "landing-desktop-ru.png",
    locale: "ru",
    viewport: { width: 1440, height: 1000 },
  },
  {
    name: "landing-mobile-ru.png",
    locale: "ru",
    viewport: { width: 390, height: 844 },
  },
  {
    name: "landing-desktop-uz.png",
    locale: "uz",
    viewport: { width: 1440, height: 1000 },
  },
  {
    name: "landing-desktop-en.png",
    locale: "en",
    viewport: { width: 1440, height: 1000 },
  },
];

for (const capture of captures) {
  test(`@screenshots ${capture.name}`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize(capture.viewport);
    await page.goto(`/${capture.locale}`);
    await expect(page.locator("h1")).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({
      path: path.join(screenshotDirectory, capture.name),
      fullPage: true,
    });
  });
}
