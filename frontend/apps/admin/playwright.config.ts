import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  outputDir: "../../test-results/admin",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: [
    ["list"],
    ["html", { outputFolder: "../../playwright-report/admin", open: "never" }],
  ],
  use: {
    baseURL: "http://localhost:3002",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "sh scripts/run-e2e-backend.sh",
      url: "http://localhost:8012/health/live",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command:
        "NEXT_PUBLIC_INTERNAL_API_URL=http://localhost:8012/api/v1/internal pnpm exec next dev --port 3002",
      url: "http://localhost:3002/en/login",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
