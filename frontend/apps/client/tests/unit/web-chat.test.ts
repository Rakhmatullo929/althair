import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "@workspace/api-client";

describe("public Web Chat contracts", () => {
  it("ships a host-isolated iframe loader with no embedded credential", () => {
    const loader = readFileSync(
      resolve(process.cwd(), "public/widget.js"),
      "utf8",
    );
    expect(loader).toContain('document.createElement("iframe")');
    expect(loader).toContain("dataset.installationKey");
    expect(loader).toContain("postMessage");
    expect(loader).not.toContain("session_token=");
    expect(loader).not.toContain("X-Organization-ID");
  });

  it("keeps installation management tenant scoped", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
        {
          status: 200,
          headers: { "content-type": "application/json" },
        },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = new ApiClient({ baseUrl: "https://api.example.test/api/v1" });
    api.setOrganizationId("organization-web-chat");
    await api.webChatInstallations();
    const headers = new Headers(fetchMock.mock.calls[0]![1].headers);
    expect(headers.get("X-Organization-ID")).toBe("organization-web-chat");
    expect(
      String(fetchMock.mock.calls[0]![0]).endsWith("/web-chat/installations/"),
    ).toBe(true);
    vi.unstubAllGlobals();
  });
});
