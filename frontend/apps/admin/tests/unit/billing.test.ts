import { describe, expect, it } from "vitest";
import {
  billingActionPath,
  canManageBilling,
  canReconcileBilling,
  validReviewedReason,
} from "@/lib/billing";

describe("internal billing controls", () => {
  it("limits plan and financial management to owner/admin", () => {
    expect(canManageBilling("platform_owner")).toBe(true);
    expect(canManageBilling("platform_admin")).toBe(true);
    expect(canManageBilling("support")).toBe(false);
    expect(canManageBilling("operations")).toBe(false);
  });

  it("allows operations to reconcile but not mark payments", () => {
    expect(canReconcileBilling("operations")).toBe(true);
    expect(canManageBilling("operations")).toBe(false);
  });

  it("routes each privileged action to the separate internal namespace", () => {
    expect(billingActionPath("publish", "plan-1")).toBe(
      "/billing/plans/plan-1/publish/",
    );
    expect(billingActionPath("mark-paid", "invoice-1")).toBe(
      "/billing/invoices/invoice-1/mark-paid/",
    );
    expect(billingActionPath("reconcile")).toBe("/billing/usage/reconcile/");
  });

  it("requires a meaningful reviewed reason before an action", () => {
    expect(validReviewedReason("short")).toBe(false);
    expect(validReviewedReason("Reviewed by finance")).toBe(true);
  });
});
