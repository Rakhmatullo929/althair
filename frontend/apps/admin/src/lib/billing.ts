export type BillingAdminAction =
  | "publish"
  | "grant"
  | "grace"
  | "issue"
  | "void"
  | "mark-paid"
  | "reconcile";

export function canManageBilling(role: string) {
  return role === "platform_owner" || role === "platform_admin";
}

export function canReconcileBilling(role: string) {
  return canManageBilling(role) || role === "operations";
}

export function billingActionPath(action: BillingAdminAction, id?: string) {
  if (action === "publish") return `/billing/plans/${id}/publish/`;
  if (["issue", "void", "mark-paid"].includes(action))
    return `/billing/invoices/${id}/${action}/`;
  if (action === "grant") return `/billing/subscriptions/${id}/grant/`;
  if (action === "grace") return `/billing/subscriptions/${id}/extend-grace/`;
  return "/billing/usage/reconcile/";
}

export function validReviewedReason(reason: string) {
  return reason.trim().length >= 8;
}
