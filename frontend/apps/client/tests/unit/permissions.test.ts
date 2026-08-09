import { describe, expect, it } from "vitest";
import { can, canEditWorkspace, shouldWarnUnsaved } from "@/lib/permissions";

describe("role-aware controls", () => {
  it("allows owners and admins to manage the team but not agents", () => {
    expect(can("owner", "manage_team")).toBe(true);
    expect(can("admin", "manage_team")).toBe(true);
    expect(can("agent", "manage_team")).toBe(false);
  });

  it("makes every suspended organization read-only", () => {
    expect(canEditWorkspace("owner", "suspended", "manage_company")).toBe(
      false,
    );
    expect(canEditWorkspace("owner", "active", "manage_company")).toBe(true);
  });

  it("warns only when AI Context has unsaved changes", () => {
    expect(shouldWarnUnsaved(true)).toBe(true);
    expect(shouldWarnUnsaved(false)).toBe(false);
  });
});
