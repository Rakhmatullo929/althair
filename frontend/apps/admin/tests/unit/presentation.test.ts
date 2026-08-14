import { describe, expect, it } from "vitest";
import {
  collectionFromPayload,
  displayValue,
  forbiddenDisplayTerms,
  isSafeDisplayKey,
  metricValue,
  roleCanManage,
  safeEntries,
  sectionEndpoint,
} from "@/lib/presentation";

describe("internal operations presentation policy", () => {
  it.each(forbiddenDisplayTerms)("never displays %s fields", (term) => {
    expect(isSafeDisplayKey(term)).toBe(false);
    expect(isSafeDisplayKey(`provider_${term}`)).toBe(false);
  });

  it("keeps explicitly safe operational fields", () => {
    expect(isSafeDisplayKey("safe_error_category")).toBe(true);
    expect(isSafeDisplayKey("configuration_ready")).toBe(true);
  });

  it("filters unsafe nested record keys", () => {
    expect(
      safeEntries({
        status: "ok",
        access_token: "hidden",
        transcript: "hidden",
      }),
    ).toEqual([["status", "ok"]]);
  });

  it("renders truthful value summaries", () => {
    expect(displayValue(false)).toBe("No");
    expect(displayValue(1200)).toBe("1,200");
    expect(displayValue([1, 2])).toBe("2 records");
    expect(displayValue(null)).toBe("—");
    expect(metricValue({ total: 4, active: 3, suspended: 1 })).toBe(4);
    expect(metricValue({ runs_today: 2, input_tokens_month: 4000 })).toBe(2);
    expect(metricValue([{ status: "active" }, { status: "draft" }])).toBe(2);
  });

  it("extracts stable paginated and usage collections", () => {
    expect(collectionFromPayload({ results: [{ id: "1" }] })).toHaveLength(1);
    expect(collectionFromPayload({ usage: [{ id: "2" }] })).toHaveLength(1);
    expect(collectionFromPayload({})).toEqual([]);
  });

  it("maps every operations section to the internal namespace", () => {
    expect(
      Object.values(sectionEndpoint).every((path) => path.startsWith("/")),
    ).toBe(true);
    expect(sectionEndpoint.audit).toBe("/audit/");
  });

  it("enforces role-aware action visibility", () => {
    expect(roleCanManage("platform_owner", "staff")).toBe(true);
    expect(roleCanManage("platform_admin", "organizations")).toBe(true);
    expect(roleCanManage("platform_admin", "staff")).toBe(false);
    expect(roleCanManage("operations", "jobs")).toBe(true);
    expect(roleCanManage("support", "incidents")).toBe(true);
    expect(roleCanManage("security_auditor", "incidents")).toBe(false);
  });
});
