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

async function tenant(request: APIRequestContext, name: string) {
  const response = await request.get(`${api}/organizations/`);
  const body = (await response.json()) as {
    results: Array<{ id: string; name: string }>;
  };
  return body.results.find((row) => row.name === name)!.id;
}

async function csrf(request: APIRequestContext) {
  return (
    (await (await request.get(`${api}/users/auth/csrf/`)).json()) as {
      csrftoken: string;
    }
  ).csrftoken;
}

test.describe.serial("tenant-safe Booking", () => {
  test("owner sees stored catalog and zero-safe calendar metrics", async ({
    page,
  }) => {
    await login(page);
    await page.getByRole("link", { name: "Booking", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "Booking & scheduling" }),
    ).toBeVisible();
    await expect(page.getByText("Today")).toBeVisible();
    await page.getByRole("link", { name: "Services", exact: true }).click();
    await expect(page.getByText("Initial consultation")).toBeVisible();
  });

  test("owner creates a service and makes an active member bookable", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/booking/services");
    await page.getByRole("button", { name: "Add service" }).click();
    await page.getByLabel("Service name").fill("E2E Follow-up visit");
    await page.getByLabel("Duration").fill("30");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("E2E Follow-up visit")).toBeVisible();

    await page.goto("/en/app/booking/staff");
    await page.getByRole("button", { name: "Make staff bookable" }).click();
    await page
      .getByLabel("Team member")
      .selectOption({ label: "Timur Saidov" });
    await page.getByLabel("Public display name").fill("E2E Booking Specialist");
    await page.getByLabel("Branch").selectOption({ index: 1 });
    await page.getByLabel("Supported service").selectOption({
      label: "E2E Follow-up visit",
    });
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("E2E Booking Specialist")).toBeVisible();
  });

  test("owner configures resource hours and a timezone-aware time-off exception", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/booking/schedules");
    await page.getByLabel("Schedule type").selectOption("resource");
    await page
      .getByLabel("Branch, staff, or resource")
      .selectOption({ index: 1 });
    await page.getByLabel("Weekday").selectOption("6");
    await page.getByLabel("Start time").fill("10:00");
    await page.getByLabel("End time").fill("12:00");
    await page.getByRole("button", { name: "Add weekly rule" }).click();
    await expect(page.getByText("Sunday").last()).toBeVisible();

    const date = new Date();
    date.setDate(date.getDate() + 10);
    const day = date.toISOString().slice(0, 10);
    await page.getByLabel("Starts").fill(`${day}T13:00`);
    await page.getByLabel("Ends").fill(`${day}T14:00`);
    await page.getByRole("button", { name: "Add exception" }).click();
    await expect(page.getByText("Time off").last()).toBeVisible();
  });

  test("service, staff, resource, waitlist, and reminders pages use stored data", async ({
    page,
  }) => {
    await login(page);
    for (const [path, heading] of [
      ["services", "Bookable services"],
      ["staff", "Bookable staff"],
      ["resources", "Rooms & resources"],
      ["waitlist", "Waitlist"],
      ["reminders", "Appointment reminders"],
    ]) {
      await page.goto(`/en/app/booking/${path}`);
      await expect(
        page.getByRole("heading", { name: heading, level: 2 }),
      ).toBeVisible();
    }
  });

  test("public flow shows real availability and never invents confirmation", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/booking/settings");
    await expect(page.getByText("Opaque public key")).toBeVisible();
    await page.getByRole("link", { name: "Preview" }).click();
    await expect(
      page.getByRole("heading", { name: "Book with Mehr Clinic" }),
    ).toBeVisible();
    await page.getByRole("button", { name: /Initial consultation/ }).click();
    await page.getByLabel("Location").selectOption({ index: 1 });
    await page.getByRole("button", { name: "Find available times" }).click();
    const firstSlot = page.locator(".slot-grid button").first();
    await expect(firstSlot).toBeVisible();
    await firstSlot.click();
    await page.getByLabel("Full name").fill("E2E Booking Customer");
    await page.getByLabel("Email").fill("booking-customer@example.test");
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "Reserve appointment" }).click();
    await expect(
      page.getByRole("heading", { name: "Your request is saved" }),
    ).toBeVisible();
    await expect(page.getByText(/Reference: BK-/)).toBeVisible();
    await expect(
      page.getByText("pending confirmation", { exact: true }),
    ).toHaveCount(0);
  });

  test("a stale slot is rejected and an identical retry remains idempotent", async ({
    page,
  }) => {
    await login(page);
    const organizationId = await tenant(page.request, "Mehr Clinic");
    const headers = { "X-Organization-ID": organizationId };
    const branches = (await (
      await page.request.get(
        `${api}/organizations/${organizationId}/branches/`,
        {
          headers,
        },
      )
    ).json()) as { results: Array<{ id: string }> };
    const services = (await (
      await page.request.get(`${api}/booking/services/`, { headers })
    ).json()) as { results: Array<{ id: string; name: string }> };
    const contacts = (await (
      await page.request.get(`${api}/contacts/`, { headers })
    ).json()) as { results: Array<{ id: string }> };
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    const day = tomorrow.toISOString().slice(0, 10);
    const query = new URLSearchParams({
      branch_id: branches.results[0].id,
      service_id: services.results.find(
        (service) => service.name === "Initial consultation",
      )!.id,
      date_from: day,
      date_to: day,
    });
    const availability = (await (
      await page.request.get(`${api}/booking/availability/?${query}`, {
        headers,
      })
    ).json()) as {
      results: Array<{ starts_at: string; staff_profile_id: string }>;
    };
    expect(availability.results.length).toBeGreaterThan(0);
    const slot = availability.results.at(-1)!;
    const token = await csrf(page.request);
    const body = {
      branch_id: branches.results[0].id,
      service_id: services.results.find(
        (service) => service.name === "Initial consultation",
      )!.id,
      contact_id: contacts.results[0].id,
      starts_at: slot.starts_at,
      staff_profile_id: slot.staff_profile_id,
    };
    const first = await page.request.post(`${api}/booking/holds/`, {
      headers: {
        ...headers,
        "X-CSRFToken": token,
        "Idempotency-Key": "e2e-stale-slot-first",
      },
      data: body,
    });
    expect(first.status()).toBe(201);
    const retry = await page.request.post(`${api}/booking/holds/`, {
      headers: {
        ...headers,
        "X-CSRFToken": token,
        "Idempotency-Key": "e2e-stale-slot-first",
      },
      data: body,
    });
    expect(retry.status()).toBe(200);
    expect((await retry.json()).id).toBe((await first.json()).id);
    const stale = await page.request.post(`${api}/booking/holds/`, {
      headers: {
        ...headers,
        "X-CSRFToken": token,
        "Idempotency-Key": "e2e-stale-slot-second",
      },
      data: body,
    });
    expect(stale.status()).toBe(409);
    expect((await stale.json()).code).toBe("slot_unavailable");
  });

  test("Inbox employee creates an appointment from the same real availability", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/inbox");
    await page.locator(".conversation-list button").first().click();
    const panel = page.locator(".inbox-booking-panel");
    await panel.locator(".inbox-booking-toggle").click();
    await panel.getByLabel("Service").selectOption({
      label: "Initial consultation",
    });
    await panel.getByLabel("Branch").selectOption({ index: 1 });
    await panel.getByRole("button", { name: "Find slots" }).click();
    await panel
      .getByRole("group", { name: "Available appointment slots" })
      .locator("button")
      .first()
      .click();
    await panel
      .getByRole("button", { name: "Create appointment", exact: true })
      .click();
    await expect(panel.getByText(/Appointment BK-/)).toBeVisible();
  });

  test("appointment detail reschedules from authoritative slots and cancels", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/booking/appointments");
    await page.locator(".appointment-card").first().click();
    await page.getByRole("button", { name: "Reschedule" }).click();
    await page.getByRole("button", { name: "Find real availability" }).click();
    const slot = page
      .locator(".booking-reschedule-panel .slot-grid button")
      .first();
    await expect(slot).toBeVisible();
    await Promise.all([
      page.waitForResponse(
        (response) =>
          response.url().includes("/reschedule/") &&
          response.request().method() === "POST",
      ),
      slot.click(),
    ]);
    await page.getByRole("button", { name: "Cancel appointment" }).click();
    await expect(page.getByText("cancelled", { exact: true })).toBeVisible();
  });

  test("waitlist and fake reminder failure are operationally visible", async ({
    page,
  }) => {
    await login(page);
    await page.goto("/en/app/booking/waitlist");
    await expect(
      page.getByText("active", { exact: true }).first(),
    ).toBeVisible();
    await page.goto("/en/app/booking/reminders");
    await expect(
      page.getByText("failed", { exact: true }).first(),
    ).toBeVisible();
  });

  test("tenant switching, lower roles, suspension, and cross-tenant IDs stay isolated", async ({
    page,
  }) => {
    await login(page);
    const mehr = await tenant(page.request, "Mehr Clinic");
    const atlas = await tenant(page.request, "Atlas Academy");
    const services = (await (
      await page.request.get(`${api}/booking/services/`, {
        headers: { "X-Organization-ID": mehr },
      })
    ).json()) as { results: Array<{ id: string }> };
    const crossed = await page.request.get(
      `${api}/booking/services/${services.results[0].id}/`,
      { headers: { "X-Organization-ID": atlas } },
    );
    expect(crossed.status()).toBe(404);

    await page.goto("/en/app/booking/services");
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Atlas Academy · Administrator" });
    await expect(page.getByText("Initial consultation")).toHaveCount(0);
    await page
      .getByLabel("Organization")
      .selectOption({ label: "Paused Studio · Owner" });
    await expect(page.getByText(/organization is suspended/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Add service" })).toHaveCount(
      0,
    );

    await page.locator(".user-menu summary").click();
    await page.getByRole("button", { name: "Log out" }).click();
    await login(page, "member@portal.test");
    await page.goto("/en/app/booking/services");
    await expect(page.getByRole("button", { name: "Add service" })).toHaveCount(
      0,
    );
  });

  test("Booking works in RU, UZ, EN and mobile has no serious accessibility violations", async ({
    page,
  }) => {
    await login(page);
    for (const [locale, heading] of [
      ["en", "Booking & scheduling"],
      ["ru", "Запись и расписание"],
      ["uz", "Band qilish va jadval"],
    ]) {
      await page.goto(`/${locale}/app/booking`);
      await expect(
        page.getByRole("heading", { name: heading, level: 1 }),
      ).toBeVisible();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/en/app/booking/services");
    await expect(
      page.getByRole("heading", { name: "Bookable services", level: 2 }),
    ).toBeVisible();
    const width = await page.evaluate(() => ({
      viewport: document.documentElement.clientWidth,
      content: document.documentElement.scrollWidth,
    }));
    expect(width.content).toBeLessThanOrEqual(width.viewport);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
});
