import type {
  AIDraft,
  AIHandoff,
  AIRun,
  AIRuntimeConfig,
  AIToolCall,
  AIToolPolicy,
  AIUsage,
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
  InstagramConnection,
  InstagramHealth,
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
  TelegramBotConnection,
  TelegramHealth,
  TelegramManagedBotRequest,
  TelegramReadiness,
  TelegramUserLink,
  WebChatInstallation,
  WebChatSessionSummary,
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
  sendMessage = (
    id: string,
    body: string,
    clientMessageId: string,
    humanAgent = false,
  ) =>
    this.request<ConversationMessage>(`/conversations/${id}/messages/`, {
      method: "POST",
      body: {
        body,
        client_message_id: clientMessageId,
        ...(humanAgent ? { human_agent: true } : {}),
      },
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

  aiRuntimeConfig = () => this.request<AIRuntimeConfig>("/ai/runtime-config/");
  updateAIRuntimeConfig = (body: Partial<AIRuntimeConfig>) =>
    this.request<AIRuntimeConfig>("/ai/runtime-config/", {
      method: "PATCH",
      body,
    });
  aiToolPolicies = () => this.request<AIToolPolicy[]>("/ai/tool-policies/");
  updateAIToolPolicies = (
    policies: Array<
      Pick<
        AIToolPolicy,
        "tool_name" | "enabled" | "execution_mode" | "configuration"
      >
    >,
  ) =>
    this.request<AIToolPolicy[]>("/ai/tool-policies/", {
      method: "PATCH",
      body: { policies },
    });
  aiRuns = (params?: Record<string, string | number | undefined>) =>
    this.request<Paginated<AIRun>>(`/ai/runs/${queryString(params)}`);
  aiRun = (id: string) => this.request<AIRun>(`/ai/runs/${id}/`);
  aiUsage = () => this.request<AIUsage>("/ai/usage/");
  conversationAIRuns = (id: string) =>
    this.request<Paginated<AIRun> & { handoffs: AIHandoff[] }>(
      `/conversations/${id}/ai/runs/`,
    );
  generateAIDraft = (id: string, idempotencyKey: string) =>
    this.request<AIRun>(`/conversations/${id}/ai/generate-draft/`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    });
  pauseConversationAI = (id: string) =>
    this.request<{ ai_state: Conversation["ai_state"] }>(
      `/conversations/${id}/ai/pause/`,
      { method: "POST" },
    );
  resumeConversationAI = (
    id: string,
    mode:
      | "suggest"
      | "autopilot_test"
      | "autopilot_web_chat"
      | "autopilot_instagram"
      | "autopilot_telegram",
  ) =>
    this.request<{ ai_state: Conversation["ai_state"] }>(
      `/conversations/${id}/ai/resume/`,
      { method: "POST", body: { mode } },
    );
  approveAIDraft = (id: string) =>
    this.request<AIDraft>(`/ai/drafts/${id}/approve/`, { method: "POST" });
  editAndSendAIDraft = (id: string, body: string) =>
    this.request<AIDraft>(`/ai/drafts/${id}/edit-and-send/`, {
      method: "POST",
      body: { body },
    });
  rejectAIDraft = (id: string, reason = "") =>
    this.request<AIDraft>(`/ai/drafts/${id}/reject/`, {
      method: "POST",
      body: { reason },
    });
  approveAIToolCall = (id: string) =>
    this.request<AIToolCall>(`/ai/tool-calls/${id}/approve/`, {
      method: "POST",
    });
  rejectAIToolCall = (id: string) =>
    this.request<AIToolCall>(`/ai/tool-calls/${id}/reject/`, {
      method: "POST",
    });
  acknowledgeAIHandoff = (id: string) =>
    this.request<AIHandoff>(`/ai/handoffs/${id}/acknowledge/`, {
      method: "POST",
    });
  assignAIHandoff = (id: string, membershipId: string) =>
    this.request<AIHandoff>(`/ai/handoffs/${id}/assign/`, {
      method: "POST",
      body: { membership_id: membershipId },
    });
  resolveAIHandoff = (id: string) =>
    this.request<AIHandoff>(`/ai/handoffs/${id}/resolve/`, {
      method: "POST",
    });

  instagramConnections = () =>
    this.request<Paginated<InstagramConnection>>("/integrations/instagram/");
  instagramConnection = (id: string) =>
    this.request<InstagramConnection>(`/integrations/instagram/${id}/`);
  startInstagramOAuth = (redirect: string) =>
    this.request<{
      authorization_url: string;
      state: string | null;
      expires_in: number;
      mode: "development" | "live";
    }>(`/integrations/instagram/oauth/start/${queryString({ redirect })}`);
  completeInstagramOAuth = (state: string, code: string) =>
    this.request<{ connection: InstagramConnection; redirect: string }>(
      `/integrations/instagram/oauth/callback/${queryString({ state, code })}`,
      { scope: "authenticated" },
    );
  updateInstagramConnection = (
    id: string,
    body: Pick<InstagramConnection, "automation_mode">,
  ) =>
    this.request<InstagramConnection>(`/integrations/instagram/${id}/`, {
      method: "PATCH",
      body,
    });
  disconnectInstagram = (id: string) =>
    this.request<InstagramConnection>(
      `/integrations/instagram/${id}/disconnect/`,
      {
        method: "POST",
      },
    );
  reconnectInstagram = (id: string) =>
    this.request<InstagramConnection>(
      `/integrations/instagram/${id}/reconnect/`,
      {
        method: "POST",
      },
    );
  instagramHealth = (id: string, refresh = false) =>
    this.request<InstagramHealth>(`/integrations/instagram/${id}/health/`, {
      method: refresh ? "POST" : "GET",
    });
  instagramTestEvent = (
    id: string,
    body: {
      event_type: string;
      text?: string;
      sender_id?: string;
      message_id?: string;
    },
  ) =>
    this.request<{ accepted: number; duplicates: number }>(
      `/integrations/instagram/${id}/test-event/`,
      { method: "POST", body },
    );
  instagramTestControl = (id: string, action: string) =>
    this.request<InstagramConnection>(
      `/integrations/instagram/${id}/test-control/`,
      {
        method: "POST",
        body: { action },
      },
    );

  telegramReadiness = (refresh = false) =>
    this.request<TelegramReadiness>("/integrations/telegram/readiness/", {
      method: refresh ? "POST" : "GET",
    });
  telegramIdentity = () =>
    this.request<TelegramUserLink>("/integrations/telegram/identity/");
  createTelegramIdentityLink = () =>
    this.request<TelegramUserLink>("/integrations/telegram/identity/", {
      method: "POST",
    });
  revokeTelegramIdentity = () =>
    this.request<{ revoked: number }>("/integrations/telegram/identity/", {
      method: "DELETE",
    });
  telegramManagedRequests = () =>
    this.request<TelegramManagedBotRequest[]>(
      "/integrations/telegram/managed-requests/",
    );
  createTelegramManagedRequest = (body: {
    suggested_name: string;
    suggested_username: string;
  }) =>
    this.request<{
      request: TelegramManagedBotRequest;
      creation_url: string;
    }>("/integrations/telegram/managed-requests/", { method: "POST", body });
  connectExistingTelegramBot = (token: string) =>
    this.request<TelegramBotConnection>(
      "/integrations/telegram/existing-bot/",
      { method: "POST", body: { token } },
    );
  telegramConnections = () =>
    this.request<Paginated<TelegramBotConnection>>("/integrations/telegram/");
  telegramConnection = (id: string) =>
    this.request<TelegramBotConnection>(`/integrations/telegram/${id}/`);
  updateTelegramConnection = (
    id: string,
    body: Partial<
      Pick<
        TelegramBotConnection,
        | "automation_mode"
        | "default_language"
        | "supported_languages"
        | "privacy_url"
      >
    >,
  ) =>
    this.request<TelegramBotConnection>(`/integrations/telegram/${id}/`, {
      method: "PATCH",
      body,
    });
  telegramHealth = (id: string, refresh = false) =>
    this.request<TelegramHealth>(`/integrations/telegram/${id}/health/`, {
      method: refresh ? "POST" : "GET",
    });
  rotateTelegramToken = (id: string, replacementToken = "") =>
    this.request<TelegramBotConnection>(
      `/integrations/telegram/${id}/rotate-token/`,
      { method: "POST", body: { replacement_token: replacementToken } },
    );
  updateTelegramAccess = (
    id: string,
    body: {
      access_restricted: boolean;
      permitted_telegram_user_ids: number[];
    },
  ) =>
    this.request<TelegramBotConnection>(
      `/integrations/telegram/${id}/access-settings/`,
      { method: "POST", body },
    );
  telegramAction = (id: string, action: "pause" | "reconnect" | "disconnect") =>
    this.request<TelegramBotConnection>(
      `/integrations/telegram/${id}/${action}/`,
      { method: "POST" },
    );
  telegramTestManagerEvent = (body: Record<string, unknown>) =>
    this.request<{ accepted: number; duplicates: number }>(
      "/integrations/telegram/test-manager-event/",
      { method: "POST", body },
    );
  telegramTestEvent = (id: string, body: Record<string, unknown>) =>
    this.request<{ accepted: number; duplicates: number }>(
      `/integrations/telegram/${id}/test-event/`,
      { method: "POST", body },
    );

  webChatInstallations = () =>
    this.request<Paginated<WebChatInstallation>>("/web-chat/installations/");
  webChatInstallation = (id: string) =>
    this.request<WebChatInstallation>(`/web-chat/installations/${id}/`);
  createWebChatInstallation = (body: Partial<WebChatInstallation>) =>
    this.request<WebChatInstallation>("/web-chat/installations/", {
      method: "POST",
      body,
    });
  updateWebChatInstallation = (
    id: string,
    body: Partial<WebChatInstallation>,
  ) =>
    this.request<WebChatInstallation>(`/web-chat/installations/${id}/`, {
      method: "PATCH",
      body,
    });
  webChatInstallationAction = (
    id: string,
    action: "activate" | "pause" | "revoke" | "rotate-key",
  ) =>
    this.request<WebChatInstallation>(
      `/web-chat/installations/${id}/${action}/`,
      { method: "POST" },
    );
  webChatSessions = (id: string) =>
    this.request<Paginated<WebChatSessionSummary>>(
      `/web-chat/installations/${id}/sessions/`,
    );
  webChatMetrics = (id: string) =>
    this.request<{ events: Record<string, number> }>(
      `/web-chat/installations/${id}/metrics/`,
    );
  anonymizeWebChatSession = (installationId: string, sessionId: string) =>
    this.request<WebChatSessionSummary>(
      `/web-chat/installations/${installationId}/sessions/${sessionId}/anonymize/`,
      { method: "POST" },
    );
}

export function createApiClient(options?: ApiClientOptions) {
  return new ApiClient(options);
}
