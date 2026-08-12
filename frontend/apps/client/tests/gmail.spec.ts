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

async function gmailConnection(
  request: APIRequestContext,
  organizationId: string,
) {
  const response = await request.get(`${api}/integrations/gmail/`, {
    headers: { "X-Organization-ID": organizationId },
  });
  return ((await response.json()) as { results: Array<{ id: string }> })
    .results[0];
}

test.describe.serial("Gmail email integration", () => {
  test("owner completes fake OAuth and secrets stay write-only", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/gmail");
    await expect(
      page.getByText("Official Google OAuth and Gmail API"),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Choose initial synchronization" }),
    ).toBeVisible();
    await page.getByLabel("Initial sync mode").selectOption("recent");
    await page.getByLabel("Maximum recent messages").fill("25");
    await page.getByRole("button", { name: "Connect Google mailbox" }).click();
    await expect(
      page.getByRole("heading", { name: "support@example.test" }),
    ).toBeVisible();
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    const detail = await page.request.get(
      `${api}/integrations/gmail/${connection.id}/`,
      { headers: tenantContext.headers },
    );
    const text = await detail.text();
    expect(text).not.toContain("fake-access");
    expect(text).not.toContain("fake-refresh");
    expect(text).not.toContain("encrypted_credentials");
    expect(text).toContain('"initial_sync_max_messages":25');

    const duplicateStart = await page.request.get(
      `${api}/integrations/gmail/oauth/start/?redirect=/en/app/settings/channels/gmail`,
      { headers: tenantContext.headers },
    );
    const duplicateState = (await duplicateStart.json()) as { state: string };
    const duplicate = await page.request.get(
      `${api}/integrations/gmail/oauth/callback/?state=${duplicateState.state}&code=fake_connect%3Asupport%40example.test%3ADuplicate`,
    );
    expect(duplicate.status()).toBe(409);
  });

  test("inbound email creates CRM conversation and manual threaded reply", async ({
    page,
  }) => {
    await login(page);
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    const inbound = await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-inbound/`,
      {
        headers: tenantContext.headers,
        data: {
          message_id: "gmail-e2e-first",
          thread_id: "gmail-e2e-thread",
          sender: "Gmail Visitor <gmail-visitor@example.test>",
          subject: "Question from email",
          text: "Can your team help by email?",
        },
      },
    );
    expect(inbound.status()).toBe(202);
    const duplicate = await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-inbound/`,
      {
        headers: tenantContext.headers,
        data: {
          message_id: "gmail-e2e-first",
          thread_id: "gmail-e2e-thread",
          sender: "Gmail Visitor <gmail-visitor@example.test>",
          text: "duplicate",
        },
      },
    );
    expect((await duplicate.json()).imported).toBe(0);
    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /Gmail Visitor/ })
      .first()
      .click();
    await expect(page.getByText("Can your team help by email?")).toBeVisible();
    await expect(
      page.getByText("Gmail thread reply is available"),
    ).toBeVisible();
    await expect(page.getByText("Participants:")).toBeVisible();
    await page
      .getByPlaceholder("finance@example.com, manager@example.com")
      .fill("finance@example.test");
    await page
      .getByPlaceholder("Write a plain-text reply…")
      .fill("We can help by email.");
    await page.getByRole("button", { name: "Send reply" }).click();
    await expect(
      page.getByLabel("Active conversation").getByText("We can help by email."),
    ).toBeVisible();
    await expect(page.getByText("AI paused after a human reply")).toBeVisible();
  });

  test("authenticated Pub/Sub performs idempotent partial sync and HTML stays plain", async ({
    page,
  }) => {
    await login(page);
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    const seeded = await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-inbound/`,
      {
        headers: tenantContext.headers,
        data: {
          message_id: "gmail-pubsub-message",
          thread_id: "gmail-pubsub-thread",
          sender: "Push Customer <push-customer@example.test>",
          subject: "Authenticated push",
          text: "<p>Safe HTML body</p><script>window.evil = true</script>",
          html: true,
          defer_sync: true,
        },
      },
    );
    expect(seeded.status()).toBe(202);
    const seededPayload = (await seeded.json()) as { history_id: string };
    const envelope = {
      subscription: "projects/e2e/subscriptions/gmail-push",
      message: {
        messageId: "gmail-e2e-pubsub-1",
        data: Buffer.from(
          JSON.stringify({
            emailAddress: "support@example.test",
            historyId: seededPayload.history_id,
          }),
        ).toString("base64url"),
      },
    };
    const pushed = await page.request.post(
      `${api}/webhooks/google/gmail-pubsub/`,
      {
        headers: {
          Authorization: "Bearer test-only-google-pubsub-oidc",
        },
        data: envelope,
      },
    );
    expect(pushed.status()).toBe(202);
    expect((await pushed.json()).accepted).toBe(1);
    const duplicate = await page.request.post(
      `${api}/webhooks/google/gmail-pubsub/`,
      {
        headers: {
          Authorization: "Bearer test-only-google-pubsub-oidc",
        },
        data: envelope,
      },
    );
    expect((await duplicate.json()).duplicates).toBe(1);

    await page.goto("/en/app/inbox");
    await page
      .getByRole("button", { name: /Push Customer/ })
      .first()
      .click();
    await expect(page.getByText("Safe HTML body")).toBeVisible();
    const conversation = page.getByLabel("Active conversation");
    await expect(conversation.locator("script")).toHaveCount(0);
    await expect(conversation.getByText(/window\.evil/)).toHaveCount(0);
  });

  test("automated email is ingested without exposing an AI auto-reply", async ({
    page,
  }) => {
    await login(page);
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    const inbound = await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-inbound/`,
      {
        headers: tenantContext.headers,
        data: {
          message_id: "gmail-e2e-auto",
          thread_id: "gmail-e2e-auto-thread",
          sender: "no-reply@example.test",
          subject: "Automated notice",
          text: "System notice",
          automated: true,
        },
      },
    );
    expect(inbound.status()).toBe(202);
    const rows = await page.request.get(
      `${api}/conversations/?channel_type=gmail`,
      { headers: tenantContext.headers },
    );
    const payload = (await rows.json()) as {
      results: Array<{ external_thread_id: string }>;
    };
    expect(
      payload.results.some(
        (item) => item.external_thread_id === "gmail-e2e-auto-thread",
      ),
    ).toBe(true);
  });

  test("Gmail settings are accessible and usable on mobile", async ({
    page,
  }) => {
    await login(page);
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    await page.goto(`/en/app/settings/channels/gmail/${connection.id}`);
    await expect(page.getByText("Encrypted · write-only")).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Verification checklist" }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "This checklist documents readiness; it does not claim or guarantee Google approval.",
      ),
    ).toBeVisible();
    const violations = await new AxeBuilder({ page }).analyze();
    expect(violations.violations).toEqual([]);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await expect(
      page.getByRole("heading", { name: "support@example.test" }),
    ).toBeVisible();
    expect(
      await page
        .locator("body")
        .evaluate((body) => body.scrollWidth <= window.innerWidth + 1),
    ).toBe(true);
  });

  test("revoked token, expired watch, health recovery and reconnect are actionable", async ({
    page,
  }) => {
    await login(page);
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    const revoked = await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-state/`,
      { headers: tenantContext.headers, data: { state: "revoked" } },
    );
    expect(revoked.status()).toBe(200);
    await page.goto(`/en/app/settings/channels/gmail/${connection.id}`);
    await expect(
      page.getByRole("button", { name: "Reconnect Google" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Reconnect Google" }).click();
    await expect(page.getByText("Gmail settings updated.")).toBeVisible();

    const expired = await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-state/`,
      { headers: tenantContext.headers, data: { state: "watch_expired" } },
    );
    expect(expired.status()).toBe(200);
    await page.reload();
    await expect(
      page.getByText("watch expired", { exact: true }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Renew watch" }).click();
    await expect(page.getByText("Gmail settings updated.")).toBeVisible();
  });

  test("Gmail suggest mode creates an approvable draft and sends through the backend", async ({
    page,
  }) => {
    await login(page);
    const tenantContext = await tenant(page.request);
    const connectionList = (await (
      await page.request.get(`${api}/integrations/gmail/`, {
        headers: tenantContext.headers,
      })
    ).json()) as {
      results: Array<{ id: string; channel_connection: string }>;
    };
    const connection = connectionList.results[0];
    const existingRuntime = (await (
      await page.request.get(`${api}/ai/runtime-config/`, {
        headers: tenantContext.headers,
      })
    ).json()) as { allowed_channel_connections: string[] };
    const runtime = await page.request.patch(`${api}/ai/runtime-config/`, {
      headers: tenantContext.headers,
      data: {
        enabled: true,
        default_mode: "suggest",
        allowed_channel_connections: Array.from(
          new Set([
            ...existingRuntime.allowed_channel_connections,
            connection.channel_connection,
          ]),
        ),
      },
    });
    expect(runtime.status()).toBe(200);
    const gmailMode = await page.request.patch(
      `${api}/integrations/gmail/${connection.id}/`,
      {
        headers: tenantContext.headers,
        data: { automation_mode: "suggest" },
      },
    );
    expect(gmailMode.status()).toBe(200);
    await page.request.post(
      `${api}/integrations/gmail/${connection.id}/test-inbound/`,
      {
        headers: tenantContext.headers,
        data: {
          message_id: "gmail-suggest-message",
          thread_id: "gmail-suggest-thread",
          sender: "AI Email Customer <ai-email@example.test>",
          subject: "AI email draft",
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
    await page.getByRole("button", { name: "Approve and send" }).click();
    await expect(page.getByText("AI-generated content")).toBeVisible();
    await expect(page.getByText("Response draft")).toHaveCount(0);
  });

  test("organization and role boundaries stay isolated in all three locales", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/settings/channels/gmail");
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(
      page.getByRole("heading", { name: "No Gmail mailbox" }),
    ).toBeVisible();
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Paused Studio · Owner" });
    await expect(page.getByText("Gmail settings are read-only")).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Connect Google mailbox" }),
    ).toHaveCount(0);

    await page.goto("/ru/app/settings/channels/gmail");
    await expect(
      page.getByRole("heading", { name: "Почта Gmail" }),
    ).toBeVisible();
    await page.goto("/uz/app/settings/channels/gmail");
    await expect(
      page.getByRole("heading", { name: "Gmail pochtasi" }),
    ).toBeVisible();

    await page.locator(".user-menu summary").click();
    await page.getByRole("button", { name: /log out|выйти|chiqish/i }).click();
    await login(page, "member@portal.test");
    await page.goto("/en/app/settings/channels/gmail");
    await expect(
      page.getByRole("button", { name: "Connect Google mailbox" }),
    ).toHaveCount(0);
  });

  test("unauthenticated Pub/Sub and cross-tenant detail fail closed", async ({
    page,
  }) => {
    const denied = await page.request.post(`${api}/webhooks/gmail/pubsub/`, {
      data: {},
    });
    expect(denied.status()).toBe(401);
    await login(page);
    const tenantContext = await tenant(page.request);
    const connection = await gmailConnection(page.request, tenantContext.id);
    const other = await page.request.get(`${api}/organizations/`);
    const organizations = (await other.json()) as {
      results: Array<{ id: string }>;
    };
    const otherOrganization = organizations.results.find(
      (item) => item.id !== tenantContext.id,
    );
    if (otherOrganization) {
      const response = await page.request.get(
        `${api}/integrations/gmail/${connection.id}/`,
        { headers: { "X-Organization-ID": otherOrganization.id } },
      );
      expect([403, 404]).toContain(response.status());
    }
  });
});
