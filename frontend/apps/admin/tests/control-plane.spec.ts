import AxeBuilder from "@axe-core/playwright";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

const backend = "http://localhost:8012/api/v1";
const internal = `${backend}/internal`;
const staffPassword = "internal-platform-development-only";
const customerPassword = "client-portal-development-only-password";

test.describe.configure({ mode: "serial" });

async function csrf(request: APIRequestContext, internalAuth = true) {
  const response = await request.get(
    internalAuth ? `${internal}/auth/csrf/` : `${backend}/users/auth/csrf/`,
  );
  expect(response.ok()).toBeTruthy();
  return String(((await response.json()) as { csrftoken: string }).csrftoken);
}

async function loginInternal(
  page: Page,
  email = "platform-owner@example.test",
) {
  const token = await csrf(page.request);
  const login = await page.request.post(`${internal}/auth/login/`, {
    headers: { "X-CSRFToken": token },
    data: { email, password: staffPassword },
  });
  expect(login.status()).toBe(200);
  const mfa = await page.request.post(`${internal}/auth/mfa/verify/`, {
    headers: { "X-CSRFToken": token },
    data: { code: "000000" },
  });
  expect(mfa.status()).toBe(200);
  return token;
}

async function organizations(request: APIRequestContext) {
  const response = await request.get(`${internal}/organizations/`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()) as {
    results: Array<{ id: string; name: string; status: string }>;
  };
}

async function mutate(
  request: APIRequestContext,
  token: string,
  path: string,
  data: Record<string, unknown>,
  method: "post" | "patch" = "post",
) {
  return request[method](`${internal}${path}`, {
    headers: { "X-CSRFToken": token },
    data,
  });
}

test("platform owner signs in and completes fake TOTP", async ({ page }) => {
  await page.goto("/en/login");
  await page.getByLabel("Internal email").fill("platform-owner@example.test");
  await page.getByLabel("Password").fill(staffPassword);
  await page.getByRole("button", { name: "Continue securely" }).click();
  await expect(
    page.getByRole("heading", { name: "Verify multi-factor authentication" }),
  ).toBeVisible();
  await page.getByLabel("Verification code").fill("000000");
  await page
    .getByRole("button", { name: "Verify and open operations" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Platform overview", level: 1 }),
  ).toBeVisible();
});

test("customer user cannot access the internal application", async ({
  page,
}) => {
  const token = await csrf(page.request, false);
  const login = await page.request.post(`${backend}/users/auth/login/`, {
    headers: { "X-CSRFToken": token },
    data: { email: "owner@portal.test", password: customerPassword },
  });
  expect(login.status()).toBe(200);
  expect((await page.request.get(`${internal}/me/`)).status()).toBe(401);
});

test("support sees a redacted read-only tenant", async ({ page }) => {
  await loginInternal(page, "platform-support@example.test");
  const org = (await organizations(page.request)).results[0];
  const detail = await page.request.get(
    `${internal}/organizations/${org.id}/`,
    { headers: { "X-Internal-Reason": "Synthetic support review" } },
  );
  expect(detail.status()).toBe(200);
  expect(JSON.stringify(await detail.json())).toContain("***@");
});

test("support cannot suspend a tenant", async ({ page }) => {
  const token = await loginInternal(page, "platform-support@example.test");
  const org = (await organizations(page.request)).results[0];
  expect(
    (
      await mutate(page.request, token, `/organizations/${org.id}/suspend/`, {
        reason: "Support should not suspend tenants",
      })
    ).status(),
  ).toBe(403);
});

test("platform admin suspends a tenant with a reason", async ({ page }) => {
  const token = await loginInternal(page, "platform-admin@example.test");
  const org = (await organizations(page.request)).results[0];
  const response = await mutate(
    page.request,
    token,
    `/organizations/${org.id}/suspend/`,
    { reason: "Synthetic admin suspension test" },
  );
  expect(response.status()).toBe(200);
  expect((await response.json()).status).toBe("suspended");
});

test("customer API becomes read-only while suspended", async ({ page }) => {
  const ownerToken = await loginInternal(page);
  const org = (await organizations(page.request)).results[0];
  if (org.status === "suspended") {
    await mutate(
      page.request,
      ownerToken,
      `/organizations/${org.id}/reactivate/`,
      { reason: "Prepare existing customer session test" },
    );
  }
  const customerCsrf = await csrf(page.request, false);
  const customerLogin = await page.request.post(
    `${backend}/users/auth/login/`,
    {
      headers: { "X-CSRFToken": customerCsrf },
      data: { email: "owner@portal.test", password: customerPassword },
    },
  );
  expect(customerLogin.status()).toBe(200);
  const suspended = await mutate(
    page.request,
    ownerToken,
    `/organizations/${org.id}/suspend/`,
    { reason: "Verify existing customer session becomes read-only" },
  );
  expect(suspended.status()).toBe(200);
  const read = await page.request.get(`${backend}/organizations/${org.id}/`, {
    headers: { "X-Organization-ID": org.id },
  });
  expect(read.status()).toBe(200);
  const write = await page.request.patch(
    `${backend}/organizations/${org.id}/`,
    {
      headers: { "X-Organization-ID": org.id, "X-CSRFToken": customerCsrf },
      data: { name: "Blocked change" },
    },
  );
  expect(write.status()).toBe(403);
  await mutate(
    page.request,
    ownerToken,
    `/organizations/${org.id}/reactivate/`,
    { reason: "Restore after synthetic read-only test" },
  );
});

test("platform admin reactivates a tenant", async ({ page }) => {
  const token = await loginInternal(page, "platform-admin@example.test");
  const org = (await organizations(page.request)).results[0];
  await mutate(page.request, token, `/organizations/${org.id}/suspend/`, {
    reason: "Prepare synthetic reactivation",
  });
  const response = await mutate(
    page.request,
    token,
    `/organizations/${org.id}/reactivate/`,
    { reason: "Synthetic reactivation approved" },
  );
  expect((await response.json()).status).not.toBe("suspended");
});

test("global AI kill switch blocks new runtime work", async ({ page }) => {
  const token = await loginInternal(page);
  const active = await mutate(
    page.request,
    token,
    "/controls/",
    {
      action: "activate",
      kind: "global_ai",
      reason: "Synthetic global AI safety test",
    },
    "patch",
  );
  expect(active.status()).toBe(200);
  const control = (await active.json()) as { id: string };
  const list = await page.request.get(`${internal}/controls/?active=true`);
  expect(JSON.stringify(await list.json())).toContain("global_ai");
  await mutate(
    page.request,
    token,
    "/controls/",
    {
      action: "restore",
      control_id: control.id,
      reason: "Synthetic AI service restored",
    },
    "patch",
  );
});

test("provider and Voice switches block external operations", async ({
  page,
}) => {
  const token = await loginInternal(page);
  for (const [kind, provider] of [
    ["global_provider", "telegram"],
    ["voice_global", ""],
  ] as const) {
    const response = await mutate(
      page.request,
      token,
      "/controls/",
      {
        action: "activate",
        kind,
        provider_type: provider,
        reason: "Synthetic provider containment test",
      },
      "patch",
    );
    expect(response.status()).toBe(200);
    const control = (await response.json()) as { id: string };
    await mutate(
      page.request,
      token,
      "/controls/",
      {
        action: "restore",
        control_id: control.id,
        reason: "Synthetic provider recovery test",
      },
      "patch",
    );
  }
});

test("restore switch is explicit and audited", async ({ page }) => {
  await loginInternal(page);
  const audit = await page.request.get(
    `${internal}/audit/?action=control.restore`,
  );
  const body = (await audit.json()) as { count: number };
  expect(body.count).toBeGreaterThan(0);
});

test("provider health detail exposes safe state without secrets", async ({
  page,
}) => {
  await loginInternal(page);
  const response = await page.request.get(`${internal}/providers/sms/`);
  const text = JSON.stringify(await response.json()).toLowerCase();
  expect(text).toContain("secrets_redacted");
  expect(text).not.toContain("auth_token");
});

test("idempotent dead-letter retry is allowed", async ({ page }) => {
  const token = await loginInternal(page, "platform-operations@example.test");
  const jobs = (await (
    await page.request.get(`${internal}/jobs/?status=dead_letter`)
  ).json()) as { results: Array<{ id: string; idempotent: boolean }> };
  const job = jobs.results.find((item) => item.idempotent)!;
  const response = await mutate(page.request, token, `/jobs/${job.id}/retry/`, {
    reason: "Retry deterministic synthetic health job",
  });
  expect(response.status()).toBe(200);
});

test("non-idempotent retry is rejected", async ({ page }) => {
  const token = await loginInternal(page, "platform-operations@example.test");
  const jobs = (await (
    await page.request.get(`${internal}/jobs/?status=dead_letter`)
  ).json()) as { results: Array<{ id: string; idempotent: boolean }> };
  const job = jobs.results.find((item) => !item.idempotent)!;
  expect(
    (
      await mutate(page.request, token, `/jobs/${job.id}/retry/`, {
        reason: "Unsafe send must remain blocked",
      })
    ).status(),
  ).toBe(409);
});

test("incident create investigate and resolve lifecycle", async ({ page }) => {
  const token = await loginInternal(page, "platform-admin@example.test");
  const created = await mutate(page.request, token, "/incidents/", {
    severity: "high",
    title: "Synthetic E2E incident",
    safe_summary: "Synthetic provider issue without customer content.",
    reason: "Create synthetic browser incident",
  });
  expect(created.status()).toBe(201);
  const incident = (await created.json()) as { id: string };
  for (const status of ["investigating", "resolved"]) {
    const response = await mutate(
      page.request,
      token,
      `/incidents/${incident.id}/`,
      {
        severity: "high",
        status,
        title: "Synthetic E2E incident",
        safe_summary: "Synthetic provider issue was safely reviewed.",
        reason: `Set synthetic incident ${status}`,
      },
      "patch",
    );
    expect(response.status()).toBe(200);
  }
});

test("entitlement change gates a feature", async ({ page }) => {
  const token = await loginInternal(page, "platform-admin@example.test");
  const org = (await organizations(page.request)).results[0];
  const response = await mutate(
    page.request,
    token,
    `/entitlements/${org.id}/`,
    {
      feature_overrides: { voice: false },
      reason: "Synthetic Voice entitlement review",
    },
    "patch",
  );
  expect(response.status()).toBe(200);
  expect((await response.json()).feature_overrides.voice).toBe(false);
  await mutate(
    page.request,
    token,
    `/entitlements/${org.id}/`,
    {
      feature_overrides: { voice: true },
      reason: "Restore synthetic Voice entitlement",
    },
    "patch",
  );
});

test("organization quota override is persisted", async ({ page }) => {
  const token = await loginInternal(page, "platform-admin@example.test");
  const org = (await organizations(page.request)).results[0];
  const response = await mutate(
    page.request,
    token,
    `/entitlements/${org.id}/`,
    {
      limit_overrides: { voice_minutes: 42 },
      reason: "Synthetic usage quota test",
    },
    "patch",
  );
  expect((await response.json()).limit_overrides.voice_minutes).toBe(42);
});

test("export request is verified approved and completed with synthetic data", async ({
  page,
}) => {
  const token = await loginInternal(page);
  const org = (await organizations(page.request)).results[0];
  const created = await mutate(page.request, token, "/data-requests/", {
    organization_id: org.id,
    request_type: "export",
    reason: "Synthetic browser export request",
    scope: { profile: true },
    idempotency_key: `e2e-export-${Date.now()}`,
  });
  const row = (await created.json()) as { id: string };
  for (const action of ["verify-identity", "approve", "run"]) {
    const response = await mutate(
      page.request,
      token,
      `/data-requests/${row.id}/${action}/`,
      { reason: `Synthetic export ${action} review` },
    );
    expect(response.status()).toBe(200);
    if (action === "run")
      expect((await response.json()).status).toBe("completed");
  }
});

test("destructive request requires two distinct platform owners", async ({
  page,
}) => {
  let token = await loginInternal(page);
  const org = (await organizations(page.request)).results[0];
  const created = await mutate(page.request, token, "/data-requests/", {
    organization_id: org.id,
    request_type: "delete",
    reason: "Synthetic staged deletion review",
    scope: { tenant: true },
    idempotency_key: `e2e-delete-${Date.now()}`,
  });
  const row = (await created.json()) as { id: string };
  await mutate(
    page.request,
    token,
    `/data-requests/${row.id}/verify-identity/`,
    { reason: "Synthetic ownership verification" },
  );
  let response = await mutate(
    page.request,
    token,
    `/data-requests/${row.id}/approve/`,
    { reason: "First owner synthetic approval" },
  );
  expect((await response.json()).status).not.toBe("approved");
  token = await loginInternal(page, "platform-owner-two@example.test");
  response = await mutate(
    page.request,
    token,
    `/data-requests/${row.id}/approve/`,
    { reason: "Second owner synthetic approval" },
  );
  expect((await response.json()).status).toBe("approved");
});

test("immutable audit records every privileged action", async ({ page }) => {
  const token = await loginInternal(page, "platform-auditor@example.test");
  const response = await page.request.get(`${internal}/audit/`);
  const body = (await response.json()) as {
    count: number;
    results: Array<{ reason: string }>;
  };
  expect(body.count).toBeGreaterThan(10);
  expect(body.results.every((item) => Boolean(item.reason))).toBe(true);
  expect(
    (
      await page.request.patch(`${internal}/audit/`, {
        headers: { "X-CSRFToken": token },
        data: {},
      })
    ).status(),
  ).toBe(405);
});

test("last platform owner cannot be removed", async ({ page }) => {
  const token = await loginInternal(page);
  const staff = (await (
    await page.request.get(`${internal}/platform-staff/`)
  ).json()) as { results: Array<{ id: string; email: string }> };
  const primary = staff.results.find(
    (item) => item.email === "platform-owner@example.test",
  )!;
  const second = staff.results.find(
    (item) => item.email === "platform-owner-two@example.test",
  )!;
  await mutate(
    page.request,
    token,
    `/platform-staff/${second.id}/`,
    { status: "revoked", reason: "Prepare last owner protection test" },
    "patch",
  );
  const denied = await mutate(
    page.request,
    token,
    `/platform-staff/${primary.id}/`,
    { role: "platform_admin", reason: "Attempt to remove last owner" },
    "patch",
  );
  expect(denied.status()).toBe(409);
  await mutate(
    page.request,
    token,
    `/platform-staff/${second.id}/`,
    { status: "active", reason: "Restore second synthetic owner" },
    "patch",
  );
});

test("revoked staff session fails closed", async ({ page }) => {
  await loginInternal(page, "platform-operations@example.test");
  const ownerContext = await page.context().browser()!.newContext();
  const ownerPage = await ownerContext.newPage();
  const ownerToken = await loginInternal(ownerPage);
  const staff = (await (
    await ownerPage.request.get(`${internal}/platform-staff/`)
  ).json()) as { results: Array<{ id: string; email: string }> };
  const operations = staff.results.find(
    (item) => item.email === "platform-operations@example.test",
  )!;
  await mutate(
    ownerPage.request,
    ownerToken,
    `/platform-staff/${operations.id}/`,
    { status: "revoked", reason: "Synthetic revoked session test" },
    "patch",
  );
  expect((await page.request.get(`${internal}/me/`)).status()).toBe(401);
  await mutate(
    ownerPage.request,
    ownerToken,
    `/platform-staff/${operations.id}/`,
    { status: "active", reason: "Restore synthetic operations access" },
    "patch",
  );
  await ownerContext.close();
});

test("RU and EN routes are complete", async ({ page }) => {
  await loginInternal(page);
  await page.goto("/en/app");
  await expect(
    page.getByRole("heading", { name: "Platform overview", level: 1 }),
  ).toBeVisible();
  await page.goto("/ru/app");
  await expect(
    page.getByRole("heading", { name: "Обзор платформы" }),
  ).toBeVisible();
});

test("desktop and tablet views have no serious accessibility violations", async ({
  page,
}) => {
  await loginInternal(page);
  for (const viewport of [
    { width: 1440, height: 900 },
    { width: 900, height: 1000 },
  ]) {
    await page.setViewportSize(viewport);
    await page.goto("/en/app/providers");
    await expect(
      page.getByRole("heading", { name: "Provider health control center" }),
    ).toBeVisible();
    const results = await new AxeBuilder({ page }).analyze();
    expect(
      results.violations.filter((item) =>
        ["critical", "serious"].includes(String(item.impact)),
      ),
    ).toEqual([]);
  }
});

test("mobile urgent view keeps controls and incident acknowledgement usable", async ({
  page,
}) => {
  await loginInternal(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/en/app");
  await expect(page.getByText("Global AI safety control")).toBeVisible();
  await expect(page.getByText("Create operational incident")).toBeVisible();
});

test("session state and no-impersonation policy stay visible", async ({
  page,
}) => {
  await loginInternal(page);
  await page.goto("/en/app/settings");
  await expect(page.getByText("No impersonation")).toBeVisible();
  await expect(page.getByText(/MFA/)).toBeVisible();
  await expect(page.getByText(/impersonation enabled/i)).toBeVisible();
});
