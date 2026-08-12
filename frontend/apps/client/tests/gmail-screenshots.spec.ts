import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";

const api = "http://localhost:8011/api/v1";
const screenshotDir = resolve(
  process.cwd(),
  "../../artifacts/screenshots/gmail",
);
const screenshotStyle = "nextjs-portal { display: none !important; }";

async function login(page: Page) {
  await page.goto("/en/login");
  await page.getByLabel("Email").fill("owner@portal.test");
  await page
    .getByLabel("Password", { exact: true })
    .fill("client-portal-development-only-password");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "CRM overview" }),
  ).toBeVisible();
}

async function tenant(request: APIRequestContext) {
  const organizations = (await (
    await request.get(`${api}/organizations/`)
  ).json()) as { results: Array<{ id: string; name: string }> };
  const id = organizations.results.find(
    (item) => item.name === "Mehr Clinic",
  )!.id;
  const csrf = (await (
    await request.get(`${api}/users/auth/csrf/`)
  ).json()) as { csrftoken: string };
  return {
    id,
    headers: { "X-Organization-ID": id, "X-CSRFToken": csrf.csrftoken },
  };
}

test("@screenshots Gmail evidence", async ({ page }) => {
  test.setTimeout(120_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.goto("/en/app/settings/channels/gmail");
  await page.screenshot({
    path: resolve(screenshotDir, "01-gmail-oauth-setup.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  if (
    await page
      .getByRole("button", { name: "Connect Google mailbox" })
      .isVisible()
  ) {
    await page.getByRole("button", { name: "Connect Google mailbox" }).click();
  }
  await expect(
    page.getByRole("heading", { name: "support@example.test" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "02-gmail-connected.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  const context = await tenant(page.request);
  const list = (await (
    await page.request.get(`${api}/integrations/gmail/`, {
      headers: context.headers,
    })
  ).json()) as {
    results: Array<{ id: string; channel_connection: string }>;
  };
  const connection = list.results[0];
  const connectionId = connection.id;
  await page.goto(`/en/app/settings/channels/gmail/${connectionId}`);
  await expect(
    page.getByRole("heading", { name: "support@example.test" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "03-gmail-health-and-sync.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.post(
    `${api}/integrations/gmail/${connectionId}/test-inbound/`,
    {
      headers: context.headers,
      data: {
        message_id: "gmail-screenshot-message",
        thread_id: "gmail-screenshot-thread",
        sender: "Email Customer <email-customer@example.test>",
        subject: "Gmail screenshot workflow",
        text: "I need help from the team by email.",
        attachment: true,
      },
    },
  );
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /Email Customer/ })
    .first()
    .click();
  await expect(
    page
      .getByLabel("Active conversation")
      .getByText("I need help from the team by email."),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "04-gmail-unified-inbox.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page
    .getByPlaceholder("finance@example.com, manager@example.com")
    .fill("finance@example.test");
  await page
    .getByPlaceholder("Write a plain-text reply…")
    .fill("A manual threaded Gmail reply from the team.");
  await page.getByRole("button", { name: "Send reply" }).click();
  await expect(
    page
      .getByLabel("Active conversation")
      .getByText("A manual threaded Gmail reply from the team."),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "05-gmail-manual-reply.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await page.request.post(
    `${api}/integrations/gmail/${connectionId}/test-state/`,
    { headers: context.headers, data: { state: "watch_expired" } },
  );
  await page.goto(`/en/app/settings/channels/gmail/${connectionId}`);
  await expect(page.getByText("watch expired", { exact: true })).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "06-gmail-watch-expired.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.post(
    `${api}/integrations/gmail/${connectionId}/test-state/`,
    { headers: context.headers, data: { state: "revoked" } },
  );
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Reconnect Google" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "07-gmail-auth-revoked.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.request.post(
    `${api}/integrations/gmail/${connectionId}/test-state/`,
    { headers: context.headers, data: { state: "healthy" } },
  );
  await page.request.post(
    `${api}/integrations/gmail/${connectionId}/watch/renew/`,
    { headers: context.headers },
  );
  await page.request.patch(`${api}/ai/runtime-config/`, {
    headers: context.headers,
    data: {
      enabled: true,
      default_mode: "suggest",
      allowed_channel_connections: [connection.channel_connection],
    },
  });
  await page.request.patch(`${api}/integrations/gmail/${connectionId}/`, {
    headers: context.headers,
    data: { automation_mode: "suggest" },
  });
  await page.request.post(
    `${api}/integrations/gmail/${connectionId}/test-inbound/`,
    {
      headers: context.headers,
      data: {
        message_id: "gmail-screenshot-ai",
        thread_id: "gmail-screenshot-ai-thread",
        sender: "AI Email Customer <ai-screenshot@example.test>",
        subject: "AI draft screenshot",
        text: "Hello, which services are available?",
      },
    },
  );
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /AI Email Customer/ })
    .first()
    .click();
  await expect(page.getByText("Response draft")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "08-gmail-ai-draft.png"),
    fullPage: true,
    style: screenshotStyle,
  });

  await page.goto(`/ru/app/settings/channels/gmail/${connectionId}`);
  await expect(
    page.getByRole("heading", { name: "support@example.test" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "09-gmail-ru.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.goto(`/uz/app/settings/channels/gmail/${connectionId}`);
  await expect(
    page.getByRole("heading", { name: "support@example.test" }),
  ).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "10-gmail-uz.png"),
    fullPage: true,
    style: screenshotStyle,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /AI Email Customer/ })
    .first()
    .click();
  await expect(page.getByText("Response draft")).toBeVisible();
  await page.screenshot({
    path: resolve(screenshotDir, "11-gmail-mobile-inbox.png"),
    fullPage: true,
    style: screenshotStyle,
  });
});
