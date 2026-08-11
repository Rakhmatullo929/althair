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
  "../../artifacts/screenshots/instagram",
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
  ).json()) as {
    results: Array<{ id: string; name: string }>;
  };
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

async function shot(page: Page, name: string) {
  await page.screenshot({
    path: resolve(screenshotDir, name),
    fullPage: true,
    style: screenshotStyle,
  });
}

test("@screenshots Instagram evidence", async ({ page }) => {
  test.setTimeout(120_000);
  await mkdir(screenshotDir, { recursive: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  await page.goto("/en/app/settings/channels/instagram");
  await shot(page, "01-instagram-connection-setup.png");
  await page.getByRole("button", { name: "Connect Instagram" }).click();
  await expect(
    page.getByRole("heading", { name: "@althair_demo" }),
  ).toBeVisible();
  await shot(page, "02-instagram-connected.png");
  const context = await tenant(page.request);
  const connections = (await (
    await page.request.get(`${api}/integrations/instagram/`, {
      headers: { "X-Organization-ID": context.id },
    })
  ).json()) as { results: Array<{ id: string }> };
  const connectionId = connections.results[0].id;
  await page.goto(`/en/app/settings/channels/instagram/${connectionId}`);
  await shot(page, "03-instagram-connected-health.png");
  await shot(page, "04-instagram-app-review-checklist.png");

  await page.request.post(
    `${api}/integrations/instagram/${connectionId}/test-control/`,
    { headers: context.headers, data: { action: "permission_missing" } },
  );
  await page.reload();
  await shot(page, "05-instagram-permission-missing.png");
  await page.request.post(
    `${api}/integrations/instagram/${connectionId}/test-control/`,
    { headers: context.headers, data: { action: "restore" } },
  );

  for (const [event_type, sender_id, message_id, text] of [
    [
      "story_reply",
      "ig_screen_story",
      "screen_story",
      "A customer replied to a story",
    ],
    [
      "shared_post",
      "ig_screen_shared",
      "screen_shared",
      "A customer shared a post",
    ],
    ["message", "ig_screen_failed", "screen_failed", "Can you help me?"],
  ] as const) {
    await page.request.post(
      `${api}/integrations/instagram/${connectionId}/test-event/`,
      {
        headers: context.headers,
        data: { event_type, sender_id, message_id, text },
      },
    );
  }
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /Instagram user _story/ })
    .first()
    .click();
  await shot(page, "06-instagram-inbox-story-reply.png");
  await expect(
    page.getByText("Can reply inside the standard window"),
  ).toBeVisible();
  await shot(page, "07-instagram-standard-window-active.png");
  await page
    .getByRole("button", { name: /Instagram user shared/ })
    .first()
    .click();
  await shot(page, "08-instagram-shared-post.png");

  await page
    .getByRole("button", { name: /Instagram user failed/ })
    .first()
    .click();
  await page
    .getByPlaceholder("Write a plain-text reply…")
    .fill("[meta-policy-error]");
  await page.getByRole("button", { name: "Send reply" }).click();
  await page.reload();
  await page
    .getByRole("button", { name: /Instagram user failed/ })
    .first()
    .click();
  await shot(page, "09-instagram-failed-send.png");

  const rows = (await (
    await page.request.get(`${api}/conversations/?channel_type=instagram`, {
      headers: { "X-Organization-ID": context.id },
    })
  ).json()) as { results: Array<{ id: string; contact_name: string }> };
  const storyConversation = rows.results.find((item) =>
    item.contact_name.endsWith("story"),
  )!;
  await page.request.post(
    `${api}/integrations/instagram/${connectionId}/test-control/`,
    {
      headers: context.headers,
      data: { action: "expire_window", conversation_id: storyConversation.id },
    },
  );
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /Instagram user _story/ })
    .first()
    .click();
  await shot(page, "10-instagram-window-expired.png");
  await page.request.post(
    `${api}/integrations/instagram/${connectionId}/test-control/`,
    { headers: context.headers, data: { action: "approve_human_agent" } },
  );
  await page.reload();
  await page
    .getByRole("button", { name: /Instagram user _story/ })
    .first()
    .click();
  await shot(page, "11-instagram-human-agent-manual.png");

  await page.goto("/en/app/settings/ai-context");
  if (!(await page.getByText(/Version 1/).count())) {
    await page.getByRole("button", { name: "Publish version" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Publish version" })
      .click();
    await expect(page.getByText("published", { exact: true })).toBeVisible();
  }
  const runtimeResponse = await page.request.patch(
    `${api}/ai/runtime-config/`,
    {
      headers: context.headers,
      data: {
        enabled: true,
        default_mode: "suggest",
        allowed_channel_connections: [],
      },
    },
  );
  expect(runtimeResponse.status()).toBe(200);
  await page.goto(`/en/app/settings/channels/instagram/${connectionId}`);
  await page.getByRole("button", { name: /AI suggestions/ }).click();
  await expect(page.getByText("Instagram settings saved")).toBeVisible();
  await page.request.post(
    `${api}/integrations/instagram/${connectionId}/test-event/`,
    {
      headers: context.headers,
      data: {
        event_type: "message",
        sender_id: "ig_screen_ai",
        message_id: "screen_ai",
        text: "What services do you offer?",
      },
    },
  );
  await page.goto("/en/app/inbox");
  await page
    .getByRole("button", { name: /Instagram user een_ai/ })
    .first()
    .click();
  await expect(page.getByText("Response draft", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  await shot(page, "12-instagram-ai-draft.png");

  await page.request.post(
    `${api}/integrations/instagram/${connectionId}/test-control/`,
    { headers: context.headers, data: { action: "expire_token" } },
  );
  await page.goto(`/en/app/settings/channels/instagram/${connectionId}`);
  await shot(page, "13-instagram-token-expired.png");
  await page.goto("/ru/app/settings/channels/instagram");
  await shot(page, "14-instagram-ru.png");
  await page.goto("/uz/app/settings/channels/instagram");
  await shot(page, "15-instagram-uz.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/en/app/inbox");
  await shot(page, "16-instagram-mobile-inbox.png");
});
