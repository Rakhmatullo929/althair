import type { OrganizationRole } from "@workspace/api-client";

const roleActions: Record<OrganizationRole, ReadonlySet<string>> = {
  owner: new Set([
    "read",
    "manage_company",
    "manage_team",
    "manage_channels",
    "publish_context",
  ]),
  admin: new Set([
    "read",
    "manage_company",
    "manage_team",
    "manage_channels",
    "publish_context",
  ]),
  manager: new Set(["read", "manage_company", "publish_context"]),
  agent: new Set(["read"]),
  viewer: new Set(["read"]),
};

export function can(role: OrganizationRole | undefined, action: string) {
  return role ? roleActions[role].has(action) : false;
}

export function canEditWorkspace(
  role: OrganizationRole | undefined,
  status: "trial" | "active" | "suspended" | "archived" | undefined,
  action: string,
) {
  return status !== "suspended" && status !== "archived" && can(role, action);
}

export function shouldWarnUnsaved(isDirty: boolean) {
  return isDirty;
}
