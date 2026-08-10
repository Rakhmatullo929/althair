import type {
  AssistantContextFields,
  AssistantProfile,
  AssistantRevision,
  Branch,
  ChannelConnection,
  Contact,
  ContactIdentity,
  ContactNote,
  ContactTag,
  Conversation,
  ConversationMessage,
  CrmActivity,
  CrmOverview,
  CurrentUser,
  CursorPaginated,
  FollowUpTask,
  Invitation,
  Lead,
  Locale,
  Membership,
  OnboardingState,
  Organization,
  OrganizationProfile,
  Overview,
  Paginated,
  Pipeline,
  PipelineStage,
} from "./types";

function queryString(
  params?: Record<string, string | number | boolean | null | undefined>,
) {
  if (!params) return "";
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "")
      query.set(key, String(value));
  }
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

type Scope = "public" | "authenticated" | "tenant";

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  scope?: Scope;
  retryAuth?: boolean;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly requestId?: string,
    public readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isValidation() {
    return this.status === 400;
  }
}

type ApiClientOptions = {
  baseUrl?: string;
  getOrganizationId?: () => string | null;
};

export class ApiClient {
  private csrfToken: string | null = null;
  private csrfPromise: Promise<string> | null = null;
  private selectedOrganizationId: string | null = null;
  private readonly baseUrl: string;
  private readonly getOrganizationId: () => string | null;

  constructor(options: ApiClientOptions = {}) {
    this.baseUrl = (
      options.baseUrl ??
      process.env.NEXT_PUBLIC_API_URL ??
      "http://localhost:8000/api/v1"
    ).replace(/\/$/, "");
    this.getOrganizationId = options.getOrganizationId ?? (() => null);
  }

  setOrganizationId(organizationId: string | null) {
    this.selectedOrganizationId = organizationId;
  }

  private async ensureCsrf() {
    if (this.csrfToken) return this.csrfToken;
    if (!this.csrfPromise) {
      this.csrfPromise = (async () => {
        const response = await fetch(`${this.baseUrl}/users/auth/csrf/`, {
          credentials: "include",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw await this.toError(response);
        const payload = (await response.json()) as { csrftoken: string };
        this.csrfToken = payload.csrftoken;
        return payload.csrftoken;
      })().finally(() => {
        this.csrfPromise = null;
      });
    }
    return this.csrfPromise;
  }

  private async toError(response: Response) {
    let payload: Record<string, unknown> = {};
    try {
      payload = (await response.json()) as Record<string, unknown>;
    } catch {
      // Deliberately avoid logging response bodies; some endpoints accept secrets.
    }
    const nested = (payload.error ?? {}) as Record<string, unknown>;
    const code = String(
      payload.code ?? nested.code ?? `http_${response.status}`,
    );
    const message = String(
      payload.detail ??
        nested.message ??
        this.defaultErrorMessage(response.status),
    );
    const requestId =
      response.headers.get("x-request-id") ??
      (payload.request_id as string | undefined);
    return new ApiError(
      message,
      response.status,
      code,
      requestId,
      nested.details ?? payload,
    );
  }

  private defaultErrorMessage(status: number) {
    if (status === 400) return "The submitted information is invalid.";
    if (status === 401) return "Please sign in to continue.";
    if (status === 403) return "You do not have permission to do that.";
    if (status === 404) return "The requested item was not found.";
    if (status === 409) return "The request conflicts with the current state.";
    if (status === 429) return "Too many requests. Please try again later.";
    return "The service could not complete the request.";
  }

  private async request<T>(
    path: string,
    options: RequestOptions = {},
  ): Promise<T> {
    const scope = options.scope ?? "tenant";
    const method = (options.method ?? "GET").toUpperCase();
    const headers = new Headers(options.headers);
    headers.set("Accept", "application/json");
    const organizationId =
      scope === "tenant"
        ? (this.selectedOrganizationId ?? this.getOrganizationId())
        : null;
    if (scope === "tenant") {
      if (!organizationId)
        throw new ApiError(
          "No organization is selected.",
          400,
          "organization_required",
        );
      headers.set("X-Organization-ID", organizationId);
    }
    if (options.body !== undefined)
      headers.set("Content-Type", "application/json");
    if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
      headers.set("X-CSRFToken", await this.ensureCsrf());
    }
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: "include",
      headers,
    });
    if (
      response.status === 401 &&
      options.retryAuth !== false &&
      !path.includes("/auth/")
    ) {
      const refreshed = await this.refresh()
        .then(() => true)
        .catch(() => false);
      if (refreshed)
        return this.request<T>(path, { ...options, retryAuth: false });
    }
    if (!response.ok) throw await this.toError(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  csrf = () => this.ensureCsrf();
  login = (body: { email: string; password: string }) =>
    this.request<CurrentUser>("/users/auth/login/", {
      method: "POST",
      body,
      scope: "public",
      retryAuth: false,
    });
  register = (body: {
    first_name: string;
    last_name?: string;
    email: string;
    password: string;
    organization_name: string;
    industry: string;
    default_language: Locale;
    timezone: string;
  }) =>
    this.request<CurrentUser & { organization_id: string }>(
      "/users/auth/register/",
      { method: "POST", body, scope: "public", retryAuth: false },
    );
  refresh = () =>
    this.request<{ detail: string }>("/users/auth/refresh/", {
      method: "POST",
      scope: "public",
      retryAuth: false,
    });
  logout = () =>
    this.request<{ detail: string }>("/users/auth/logout/", {
      method: "POST",
      scope: "authenticated",
      retryAuth: false,
    });
  me = () => this.request<CurrentUser>("/me/", { scope: "authenticated" });
  inspectInvitation = (token: string) =>
    this.request<{
      state: Invitation["status"] | "invalid";
      email?: string;
      organization_name?: string;
      role?: string;
      expires_at?: string;
    }>("/users/auth/invitations/inspect/", {
      method: "POST",
      body: { token },
      scope: "public",
      retryAuth: false,
    });
  acceptInvitation = (body: {
    token: string;
    first_name?: string;
    last_name?: string;
    password?: string;
  }) =>
    this.request<{
      detail: string;
      organization_id: string;
      membership_role: string;
    }>("/users/auth/invitations/accept/", {
      method: "POST",
      body,
      scope: "public",
      retryAuth: false,
    });
  requestPasswordReset = (email: string) =>
    this.request<{
      detail: string;
      delivery: string;
      development_reset_url?: string;
    }>("/users/auth/password-reset/request/", {
      method: "POST",
      body: { email },
      scope: "public",
      retryAuth: false,
    });
  confirmPasswordReset = (token: string, password: string) =>
    this.request<{ detail: string }>("/users/auth/password-reset/confirm/", {
      method: "POST",
      body: { token, password },
      scope: "public",
      retryAuth: false,
    });

  organizations = () =>
    this.request<Paginated<Organization>>("/organizations/", {
      scope: "authenticated",
    });
  createOrganization = (
    body: Pick<
      Organization,
      "name" | "slug" | "industry" | "default_language" | "timezone"
    >,
  ) =>
    this.request<Organization>("/organizations/", {
      method: "POST",
      body,
      scope: "authenticated",
    });
  organization = (id: string) =>
    this.request<Organization>(`/organizations/${id}/`);
  updateOrganization = (id: string, body: Partial<Organization>) =>
    this.request<Organization>(`/organizations/${id}/`, {
      method: "PATCH",
      body,
    });
  profile = (id: string) =>
    this.request<OrganizationProfile>(`/organizations/${id}/profile/`);
  updateProfile = (id: string, body: Partial<OrganizationProfile>) =>
    this.request<OrganizationProfile>(`/organizations/${id}/profile/`, {
      method: "PATCH",
      body,
    });
  overview = (id: string) =>
    this.request<Overview>(`/organizations/${id}/overview/`);
  onboarding = (id: string) =>
    this.request<OnboardingState>(`/organizations/${id}/onboarding/`);
  saveOnboarding = (id: string, body: Record<string, unknown>) =>
    this.request<OnboardingState>(`/organizations/${id}/onboarding/`, {
      method: "PATCH",
      body,
    });

  branches = (id: string) =>
    this.request<Paginated<Branch>>(`/organizations/${id}/branches/`);
  createBranch = (id: string, body: Partial<Branch>) =>
    this.request<Branch>(`/organizations/${id}/branches/`, {
      method: "POST",
      body,
    });
  updateBranch = (
    organizationId: string,
    branchId: string,
    body: Partial<Branch>,
  ) =>
    this.request<Branch>(
      `/organizations/${organizationId}/branches/${branchId}/`,
      { method: "PATCH", body },
    );
  archiveBranch = (organizationId: string, branchId: string) =>
    this.request<void>(
      `/organizations/${organizationId}/branches/${branchId}/`,
      { method: "DELETE" },
    );

  memberships = (id: string) =>
    this.request<Paginated<Membership>>(`/organizations/${id}/memberships/`);
  updateMembership = (
    organizationId: string,
    membershipId: string,
    body: Partial<Pick<Membership, "role" | "status">>,
  ) =>
    this.request<Membership>(
      `/organizations/${organizationId}/memberships/${membershipId}/`,
      { method: "PATCH", body },
    );
  invitations = (id: string) =>
    this.request<Paginated<Invitation>>(`/organizations/${id}/invitations/`);
  createInvitation = (
    id: string,
    body: { email: string; role: Exclude<Membership["role"], "owner"> },
  ) =>
    this.request<Invitation>(`/organizations/${id}/invitations/`, {
      method: "POST",
      body,
    });
  revokeInvitation = (organizationId: string, invitationId: string) =>
    this.request<Invitation>(
      `/organizations/${organizationId}/invitations/${invitationId}/`,
      { method: "PATCH", body: { status: "revoked" } },
    );

  channels = () =>
    this.request<Paginated<ChannelConnection>>("/channel-connections/");
  updateChannel = (
    id: string,
    body: Partial<
      Pick<ChannelConnection, "display_name" | "branch" | "configuration">
    >,
  ) =>
    this.request<ChannelConnection>(`/channel-connections/${id}/`, {
      method: "PATCH",
      body,
    });
  assistantContext = () =>
    this.request<AssistantProfile>("/assistant-context/");
  updateAssistantContext = (body: Partial<AssistantContextFields>) =>
    this.request<AssistantProfile>("/assistant-context/", {
      method: "PATCH",
      body,
    });
  publishAssistantContext = () =>
    this.request<{ profile: AssistantProfile; revision: AssistantRevision }>(
      "/assistant-context/publish/",
      { method: "POST" },
    );
  assistantRevisions = () =>
    this.request<AssistantRevision[]>("/assistant-context/revisions/");

  contacts = (
    params?: Record<string, string | number | boolean | null | undefined>,
  ) => this.request<Paginated<Contact>>(`/contacts/${queryString(params)}`);
  contact = (id: string) => this.request<Contact>(`/contacts/${id}/`);
  createContact = (
    body: Omit<Partial<Contact>, "identities"> & {
      display_name: string;
      identities?: Array<Partial<ContactIdentity>>;
      tag_ids?: string[];
    },
  ) => this.request<Contact>("/contacts/", { method: "POST", body });
  updateContact = (id: string, body: Partial<Contact>) =>
    this.request<Contact>(`/contacts/${id}/`, { method: "PATCH", body });
  mergeContact = (sourceId: string, survivingContactId: string) =>
    this.request<Contact>(`/contacts/${sourceId}/merge/`, {
      method: "POST",
      body: { surviving_contact_id: survivingContactId },
    });
  contactIdentities = (contactId: string) =>
    this.request<ContactIdentity[]>(`/contacts/${contactId}/identities/`);
  addContactIdentity = (
    contactId: string,
    body: Partial<ContactIdentity> &
      Pick<ContactIdentity, "type" | "raw_value">,
  ) =>
    this.request<ContactIdentity>(`/contacts/${contactId}/identities/`, {
      method: "POST",
      body,
    });
  updateContactIdentity = (
    contactId: string,
    identityId: string,
    body: Partial<ContactIdentity>,
  ) =>
    this.request<ContactIdentity>(
      `/contacts/${contactId}/identities/${identityId}/`,
      { method: "PATCH", body },
    );
  removeContactIdentity = (contactId: string, identityId: string) =>
    this.request<void>(`/contacts/${contactId}/identities/${identityId}/`, {
      method: "DELETE",
    });
  contactNotes = (contactId: string) =>
    this.request<Paginated<ContactNote>>(`/contacts/${contactId}/notes/`);
  addContactNote = (contactId: string, body: string) =>
    this.request<ContactNote>(`/contacts/${contactId}/notes/`, {
      method: "POST",
      body: { body },
    });
  tags = () => this.request<Paginated<ContactTag>>("/tags/");
  createTag = (body: Pick<ContactTag, "name" | "color_token">) =>
    this.request<ContactTag>("/tags/", { method: "POST", body });

  conversations = (
    params?: Record<string, string | number | boolean | null | undefined>,
  ) =>
    this.request<Paginated<Conversation>>(
      `/conversations/${queryString(params)}`,
    );
  conversation = (id: string) =>
    this.request<Conversation>(`/conversations/${id}/`);
  updateConversation = (id: string, body: Partial<Conversation>) =>
    this.request<Conversation>(`/conversations/${id}/`, {
      method: "PATCH",
      body,
    });
  conversationMessages = (id: string, cursor?: string) =>
    this.request<CursorPaginated<ConversationMessage>>(
      `/conversations/${id}/messages/${queryString({ cursor })}`,
    );
  sendMessage = (id: string, body: string, clientMessageId: string) =>
    this.request<ConversationMessage>(`/conversations/${id}/messages/`, {
      method: "POST",
      body: { body, client_message_id: clientMessageId },
    });
  addConversationNote = (id: string, body: string) =>
    this.request<ConversationMessage>(`/conversations/${id}/notes/`, {
      method: "POST",
      body: { body },
    });
  markConversationRead = (id: string) =>
    this.request<{ unread_count: number }>(`/conversations/${id}/mark-read/`, {
      method: "POST",
    });
  assignConversation = (id: string, membershipId: string | null) =>
    this.request<Conversation>(`/conversations/${id}/assign/`, {
      method: "POST",
      body: { membership_id: membershipId },
    });
  resolveConversation = (id: string) =>
    this.request<Conversation>(`/conversations/${id}/resolve/`, {
      method: "POST",
    });
  reopenConversation = (id: string) =>
    this.request<Conversation>(`/conversations/${id}/reopen/`, {
      method: "POST",
    });
  createTestConversation = (body: {
    display_name: string;
    identity_value?: string;
    body: string;
  }) =>
    this.request<Conversation>("/dev/test-conversations/", {
      method: "POST",
      body,
    });

  pipelines = () => this.request<Pipeline[]>("/pipelines/");
  updatePipeline = (id: string, body: Partial<Pipeline>) =>
    this.request<Pipeline>(`/pipelines/${id}/`, { method: "PATCH", body });
  updatePipelineStage = (id: string, body: Partial<PipelineStage>) =>
    this.request<PipelineStage>(`/pipeline-stages/${id}/`, {
      method: "PATCH",
      body,
    });
  leads = (
    params?: Record<string, string | number | boolean | null | undefined>,
  ) => this.request<Paginated<Lead>>(`/leads/${queryString(params)}`);
  createLead = (
    body: Partial<Lead> &
      Pick<Lead, "contact" | "title"> & { confirm_duplicate?: boolean },
  ) => this.request<Lead>("/leads/", { method: "POST", body });
  updateLead = (id: string, body: Partial<Lead>) =>
    this.request<Lead>(`/leads/${id}/`, { method: "PATCH", body });
  moveLead = (id: string, stageId: string) =>
    this.request<Lead>(`/leads/${id}/move/`, {
      method: "POST",
      body: { stage_id: stageId },
    });
  winLead = (id: string) =>
    this.request<Lead>(`/leads/${id}/win/`, { method: "POST" });
  loseLead = (id: string, lostReason: string) =>
    this.request<Lead>(`/leads/${id}/lose/`, {
      method: "POST",
      body: { lost_reason: lostReason },
    });

  followUpTasks = (
    params?: Record<string, string | number | boolean | null | undefined>,
  ) =>
    this.request<Paginated<FollowUpTask>>(
      `/follow-up-tasks/${queryString(params)}`,
    );
  createFollowUpTask = (
    body: Partial<FollowUpTask> & Pick<FollowUpTask, "title" | "due_at">,
  ) =>
    this.request<FollowUpTask>("/follow-up-tasks/", { method: "POST", body });
  updateFollowUpTask = (id: string, body: Partial<FollowUpTask>) =>
    this.request<FollowUpTask>(`/follow-up-tasks/${id}/`, {
      method: "PATCH",
      body,
    });
  crmOverview = () => this.request<CrmOverview>("/crm/overview/");
  crmActivity = (
    params?: Record<string, string | number | boolean | null | undefined>,
  ) =>
    this.request<Paginated<CrmActivity>>(
      `/crm/activity/${queryString(params)}`,
    );
}

export function createApiClient(options?: ApiClientOptions) {
  return new ApiClient(options);
}
