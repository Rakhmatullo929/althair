export type JsonRecord = Record<string, unknown>;

export class InternalApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "InternalApiError";
  }
}

const baseUrl = (
  process.env.NEXT_PUBLIC_INTERNAL_API_URL ?? "/api/v1/internal"
).replace(/\/$/, "");
let csrfToken = "";

async function parseError(response: Response) {
  let payload: JsonRecord = {};
  try {
    payload = (await response.json()) as JsonRecord;
  } catch {
    // Internal errors intentionally do not expose raw response bodies.
  }
  const nested = (payload.error ?? {}) as JsonRecord;
  return new InternalApiError(
    String(
      payload.detail ??
        nested.message ??
        "The internal operation could not be completed.",
    ),
    response.status,
    String(payload.code ?? nested.code ?? `http_${response.status}`),
    response.headers.get("x-request-id") ?? undefined,
  );
}

export async function ensureInternalCsrf() {
  if (csrfToken) return csrfToken;
  const response = await fetch(`${baseUrl}/auth/csrf/`, {
    credentials: "include",
  });
  if (!response.ok) throw await parseError(response);
  csrfToken = String(((await response.json()) as JsonRecord).csrftoken ?? "");
  return csrfToken;
}

export async function internalRequest<T = unknown>(
  path: string,
  options: Omit<RequestInit, "body"> & { body?: unknown; reason?: string } = {},
): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.reason) headers.set("X-Internal-Reason", options.reason);
  if (options.body !== undefined)
    headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRFToken", await ensureInternalCsrf());
  }
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers,
    credentials: "include",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!response.ok) throw await parseError(response);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

export type InternalMe = {
  id: string;
  email: string;
  display_name: string;
  role: string;
  status: string;
  mfa_verified: boolean;
  mfa_fresh: boolean;
  session_expires_at: string;
  environment: string;
};

export const internalApi = {
  login: (email: string, password: string) =>
    internalRequest<JsonRecord>("/auth/login/", {
      method: "POST",
      body: { email, password },
    }),
  setupMfa: () =>
    internalRequest<JsonRecord>("/auth/mfa/setup/", {
      method: "POST",
      body: {},
    }),
  verifyMfa: (code: string) =>
    internalRequest<JsonRecord>("/auth/mfa/verify/", {
      method: "POST",
      body: { code },
    }),
  logout: () => internalRequest("/auth/logout/", { method: "POST", body: {} }),
  me: () => internalRequest<InternalMe>("/me/"),
  get: <T = unknown>(path: string, reason?: string) =>
    internalRequest<T>(path, { reason }),
  mutate: <T = unknown>(
    path: string,
    body: JsonRecord,
    method = "POST",
    headers?: HeadersInit,
  ) => internalRequest<T>(path, { method, body, headers }),
};
