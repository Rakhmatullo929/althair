import { describe, expect, it } from "vitest";
import type { MembershipSummary } from "@workspace/api-client";
import { validateOrganizationSelection } from "@/lib/organization-selection";

const memberships = [
  {
    id: "m1",
    organization: "org-a",
    organization_name: "Alpha",
    organization_slug: "alpha",
    organization_status: "active",
    role: "owner",
    status: "active",
    joined_at: null,
  },
  {
    id: "m2",
    organization: "org-b",
    organization_name: "Beta",
    organization_slug: "beta",
    organization_status: "active",
    role: "viewer",
    status: "active",
    joined_at: null,
  },
] satisfies MembershipSummary[];

describe("organization selection", () => {
  it("keeps a valid stored organization", () =>
    expect(validateOrganizationSelection(memberships, "org-b")).toBe("org-b"));
  it("falls back before scoped queries can run", () =>
    expect(validateOrganizationSelection(memberships, "removed-org")).toBe(
      "org-a",
    ));
  it("returns null with no active membership", () =>
    expect(validateOrganizationSelection([], "org-a")).toBeNull());
});
