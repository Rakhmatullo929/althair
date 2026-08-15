import { describe, expect, it } from "vitest";
import {
  billingBanner,
  canManageBilling,
  enabledEntitlements,
  formatMinorMoney,
  formatUsageQuantity,
  usagePercent,
} from "@/lib/billing";

describe("billing presentation", () => {
  it("uses integer minor units without floating arithmetic", () => {
    expect(formatMinorMoney(12_345, "USD", "en")).toContain("123.45");
  });

  it.each([
    ["trialing", "trial"],
    ["past_due", "pastDue"],
    ["grace", "grace"],
    ["paused", "restricted"],
    ["active", null],
  ])("maps %s to an honest lifecycle banner", (status, expected) => {
    expect(
      billingBanner({
        status: status as never,
        grace_ends_at: null,
      }),
    ).toBe(expected);
  });

  it("restricts billing writes to customer owners and admins", () => {
    expect(canManageBilling("owner")).toBe(true);
    expect(canManageBilling("admin")).toBe(true);
    expect(canManageBilling("manager")).toBe(false);
    expect(canManageBilling("viewer")).toBe(false);
  });

  it("caps usage bars and handles unlimited-looking zero values", () => {
    expect(usagePercent("12", 10)).toBe(100);
    expect(usagePercent("4", 10)).toBe(40);
    expect(usagePercent("4", 0)).toBe(0);
  });

  it("renders exact decimal usage without database scale noise", () => {
    expect(formatUsageQuantity("84.000000")).toBe("84");
    expect(formatUsageQuantity("12.500000")).toBe("12.5");
  });

  it("shows only enabled boolean features", () => {
    const rows = [
      { feature: "sms", allowed: true, value: true },
      { feature: "voice", allowed: false, value: true },
      { feature: "max_members", allowed: true, value: 5 },
    ];
    expect(
      enabledEntitlements(rows as never).map((row) => row.feature),
    ).toEqual(["sms"]);
  });
});
