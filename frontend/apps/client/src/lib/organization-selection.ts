import type { MembershipSummary } from "@workspace/api-client";

export function validateOrganizationSelection(
  memberships: MembershipSummary[],
  storedOrganizationId: string | null,
) {
  const active = memberships.filter(
    (membership) => membership.status === "active",
  );
  return active.some(
    (membership) => membership.organization === storedOrganizationId,
  )
    ? storedOrganizationId
    : (active[0]?.organization ?? null);
}
