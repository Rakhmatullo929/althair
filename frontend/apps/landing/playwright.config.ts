import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "../../test-results/landing",
  // The hero's WebGL scene can starve four parallel browser contexts during a
  // cold Next.js dev start. Keep the regression command deterministic locally
  // and in CI without changing the Landing runtime.
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [
    ["list"],
    [
      "html",
      { outputFolder: "../../playwright-report/landing", open: "never" },
    ],
  ],
  use: {
    baseURL: "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:3000/ru",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
