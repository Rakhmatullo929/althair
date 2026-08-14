export const forbiddenDisplayTerms = [
  "password",
  "secret",
  "token",
  "credential",
  "payload",
  "message_body",
  "transcript",
  "audio",
  "prompt",
  "chain_of_thought",
  "recovery_code",
];

export function isSafeDisplayKey(key: string) {
  const normalized = key.toLowerCase();
  return !forbiddenDisplayTerms.some((term) => normalized.includes(term));
}

export function safeEntries(value: unknown): [string, unknown][] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>).filter(([key]) =>
    isSafeDisplayKey(key),
  );
}

export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number")
    return new Intl.NumberFormat("en").format(value);
  if (typeof value === "string")
    return value.length > 160 ? `${value.slice(0, 157)}…` : value;
  if (Array.isArray(value)) return `${value.length} records`;
  return `${safeEntries(value).length} fields`;
}

export function metricValue(value: unknown): number | string {
  if (Array.isArray(value)) return value.length;
  if (!value || typeof value !== "object") return displayValue(value);
  const entries = safeEntries(value);
  for (const key of ["total", "runs_today", "active_calls"]) {
    const match = entries.find(([candidate]) => candidate === key)?.[1];
    if (typeof match === "number") return match;
  }
  return entries.reduce(
    (sum, [, item]) => sum + (typeof item === "number" ? item : 0),
    0,
  );
}

export type Section =
  | "overview"
  | "organizations"
  | "providers"
  | "ai"
  | "jobs"
  | "incidents"
  | "dataRequests"
  | "entitlements"
  | "audit"
  | "staff"
  | "settings";

export const sectionEndpoint: Record<Section, string> = {
  overview: "/overview/",
  organizations: "/organizations/",
  providers: "/providers/",
  ai: "/ai/usage/",
  jobs: "/jobs/",
  incidents: "/incidents/",
  dataRequests: "/data-requests/",
  entitlements: "/organizations/",
  audit: "/audit/",
  staff: "/platform-staff/",
  settings: "/settings/",
};

const mutateRoles = new Set(["platform_owner", "platform_admin"]);

export function roleCanManage(role: string, section: Section) {
  if (role === "platform_owner") return true;
  if (mutateRoles.has(role)) return !["staff"].includes(section);
  if (role === "operations")
    return ["providers", "jobs", "incidents"].includes(section);
  if (role === "support") return section === "incidents";
  return false;
}

export function collectionFromPayload(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== "object") return [];
  const record = payload as Record<string, unknown>;
  for (const key of ["results", "usage", "channels", "controls"]) {
    if (Array.isArray(record[key])) return record[key] as unknown[];
  }
  return [];
}
