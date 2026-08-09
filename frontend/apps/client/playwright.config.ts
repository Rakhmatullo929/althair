import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  outputDir: "../../test-results/client",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../../playwright-report/client", open: "never" }],
  ],
  use: {
    baseURL: "http://localhost:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "sh scripts/run-e2e-backend.sh",
      url: "http://localhost:8011/health/live",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command:
        "NEXT_PUBLIC_API_URL=http://localhost:8011/api/v1 pnpm exec next dev --port 3001",
      url: "http://localhost:3001/en/login",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
