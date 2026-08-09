import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, type ApiError } from "@workspace/api-client";

afterEach(() => vi.unstubAllGlobals());

describe("typed API client", () => {
  it("includes the selected tenant header only on tenant requests", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ id: "u", memberships: [] }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new ApiClient({
      baseUrl: "https://api.example.test/api/v1",
      getOrganizationId: () => "org-a",
    });
    await api.channels();
    await api.me();
    expect(
      new Headers(fetchMock.mock.calls[0]![1].headers).get("X-Organization-ID"),
    ).toBe("org-a");
    expect(
      new Headers(fetchMock.mock.calls[1]![1].headers).has("X-Organization-ID"),
    ).toBe(false);
  });

  it("maps status, machine code, and request ID without logging bodies", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: "rate_limited",
          detail: "Try later",
          request_id: "req-42",
        }),
        { status: 429, headers: { "content-type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = new ApiClient({ baseUrl: "https://api.example.test/api/v1" });
    await expect(api.me()).rejects.toMatchObject({
      status: 429,
      code: "rate_limited",
      requestId: "req-42",
    } satisfies Partial<ApiError>);
  });

  it("coalesces concurrent CSRF bootstrap calls", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ csrftoken: "csrf-test-token" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ state: "pending" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    const api = new ApiClient({ baseUrl: "https://api.example.test/api/v1" });

    await Promise.all([
      api.inspectInvitation("test-token-that-is-long-enough-0001"),
      api.inspectInvitation("test-token-that-is-long-enough-0001"),
    ]);

    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).endsWith("/users/auth/csrf/"),
      ),
    ).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
