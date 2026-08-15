import type {
  BillingSubscription,
  EntitlementSnapshot,
  OrganizationRole,
} from "@workspace/api-client";

export function formatMinorMoney(
  amountMinor: number,
  currency: string,
  locale: string,
) {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
    currencyDisplay: "narrowSymbol",
  }).format(amountMinor / 100);
}

export function billingBanner(
  subscription: Pick<BillingSubscription, "status" | "grace_ends_at">,
) {
  if (subscription.status === "trialing") return "trial";
  if (subscription.status === "past_due") return "pastDue";
  if (subscription.status === "grace") return "grace";
  if (["paused", "cancelled", "expired"].includes(subscription.status))
    return "restricted";
  return null;
}

export function canManageBilling(role: OrganizationRole | undefined) {
  return role === "owner" || role === "admin";
}

export function usagePercent(used: string, included: string | number) {
  const denominator = Number(included);
  if (!Number.isFinite(denominator) || denominator <= 0) return 0;
  return Math.min(100, Math.max(0, (Number(used) / denominator) * 100));
}

export function formatUsageQuantity(value: string | number) {
  const text = String(value);
  if (!text.includes(".")) return text;
  return text.replace(/0+$/, "").replace(/\.$/, "");
}

export function enabledEntitlements(rows: EntitlementSnapshot[]) {
  return rows.filter(
    (row) => row.allowed && typeof row.value === "boolean" && row.value,
  );
}
