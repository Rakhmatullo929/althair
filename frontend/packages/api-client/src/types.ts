export type Locale = "ru" | "uz" | "en";
export type OrganizationRole =
  | "owner"
  | "admin"
  | "manager"
  | "agent"
  | "viewer";
export type OrganizationStatus = "trial" | "active" | "suspended" | "archived";

export type MembershipSummary = {
  id: string;
  organization: string;
  organization_name: string;
  organization_slug: string;
  organization_status: OrganizationStatus;
  role: OrganizationRole;
  status: "active";
  joined_at: string | null;
};

export type CurrentUser = {
  id: string;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  memberships: MembershipSummary[];
};

export type Organization = {
  id: string;
  name: string;
  slug: string;
  status: OrganizationStatus;
  industry: string;
  default_language: Locale;
  timezone: string;
  logo_url: string;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
};

export type OrganizationProfile = {
  organization: string;
  public_business_name: string;
  short_description: string;
  target_customers: string;
  products_services_summary: string;
  business_rules: string;
  preferred_communication_tone: string;
  supported_languages: Locale[];
  response_guidelines: string;
  escalation_instructions: string;
  public_contact_information: {
    website?: string;
    phone?: string;
    email?: string;
    service_area?: string;
    [key: string]: unknown;
  };
  onboarding_completion_percentage: number;
  onboarding_current_step: number;
  onboarding_completed_steps: number[];
  onboarding_completed_at: string | null;
  status: "draft" | "published";
  version: number;
  created_at: string;
  updated_at: string;
  published_at: string | null;
};

export type WorkingPeriod = { open: string; close: string };
export type WorkingHours = Partial<
  Record<"mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun", WorkingPeriod[]>
>;

export type Branch = {
  id: string;
  organization: string;
  name: string;
  address: string;
  phone: string;
  email: string;
  timezone: string;
  working_hours: WorkingHours;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type Membership = {
  id: string;
  organization: string;
  user: string;
  user_email: string;
  user_name: string;
  role: OrganizationRole;
  status: "invited" | "active" | "suspended";
  created_at: string;
  updated_at: string;
  joined_at: string | null;
};

export type Invitation = {
  id: string;
  organization: string;
  email: string;
  role: Exclude<OrganizationRole, "owner">;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  invited_by_name: string;
  accepted_at: string | null;
  created_at: string;
  updated_at: string;
  delivery?: "development_console" | "not_configured";
  invitation_url?: string;
};

export type ChannelConnection = {
  id: string;
  organization: string;
  branch: string | null;
  type:
    | "instagram"
    | "telegram"
    | "whatsapp"
    | "gmail"
    | "sms"
    | "voice"
    | "webchat"
    | "other";
  provider: string;
  display_name: string;
  external_identifier: string;
  status: "draft" | "connecting" | "active" | "error" | "disconnected";
  configuration: Record<string, unknown>;
  has_credentials: boolean;
  has_webhook_secret: boolean;
  last_error_code: string;
  last_error_message: string;
  last_synced_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AssistantContextFields = {
  assistant_name: string;
  business_summary: string;
  business_description: string;
  target_customers: string;
  products_services: string;
  service_area: string;
  supported_languages: Locale[];
  default_language: Locale;
  tone_of_voice: string;
  introduction: string;
  escalation_instructions: string;
  prohibited_topics: string;
  prohibited_actions: string;
  fallback_response: string;
  additional_instructions: string;
};

export type AssistantProfile = AssistantContextFields & {
  id: string;
  organization: string;
  status: "draft" | "published";
  version: number;
  published_snapshot: Partial<AssistantContextFields>;
  published_at: string | null;
  created_at: string;
  updated_at: string;
  updated_by_name: string | null;
};

export type AssistantRevision = {
  id: string;
  version: number;
  snapshot: AssistantContextFields;
  published_by_name: string;
  published_at: string;
};

export type OnboardingState = {
  organization: Organization;
  profile: OrganizationProfile;
  assistant_context: AssistantProfile;
  branches: Branch[];
};

export type Overview = {
  onboarding_completion_percentage: number;
  onboarding_completed_at: string | null;
  branch_count: number;
  active_member_count: number;
  configured_channel_count: number;
  ai_context_status: "draft" | "published";
  ai_context_version: number;
  recent_activity: Array<{
    type: "assistant_context_published";
    version: number;
    actor: string;
    at: string;
  }>;
};

export type Paginated<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type ContactIdentityType =
  | "phone"
  | "email"
  | "instagram"
  | "telegram"
  | "whatsapp"
  | "web_chat"
  | "external";

export type ContactIdentity = {
  id: string;
  organization: string;
  contact: string;
  type: ContactIdentityType;
  raw_value: string;
  normalized_value: string;
  external_user_id: string;
  channel_connection: string | null;
  is_primary: boolean;
  is_verified: boolean;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type ContactTag = {
  id: string;
  organization: string;
  name: string;
  color_token: string;
  created_at: string;
};

export type Contact = {
  id: string;
  organization: string;
  display_name: string;
  first_name: string;
  last_name: string;
  company_name: string;
  preferred_language: Locale;
  timezone: string;
  notes_summary: string;
  status: "active" | "archived";
  merged_into: string | null;
  identities: ContactIdentity[];
  tags: ContactTag[];
  duplicate_suggestions: Array<{
    id: string;
    display_name: string;
    company_name: string;
  }>;
  created_at: string;
  updated_at: string;
};

export type ContactNote = {
  id: string;
  contact: string;
  author_membership: string;
  author_name: string;
  body: string;
  created_at: string;
  updated_at: string;
};

export type ConversationStatus = "open" | "pending" | "resolved" | "closed";
export type ConversationPriority = "low" | "normal" | "high" | "urgent";

export type Conversation = {
  id: string;
  organization: string;
  channel_connection: string;
  channel_type: string;
  channel_name: string;
  channel_provider: string;
  external_thread_id: string;
  contact: string;
  contact_name: string;
  contact_status: "active" | "archived";
  status: ConversationStatus;
  priority: ConversationPriority;
  assignment_state: "unassigned" | "assigned";
  assigned_membership: string | null;
  assigned_name: string | null;
  automation_state: "manual" | "ai_paused" | "ai_available";
  ai_state:
    | "off"
    | "suggest"
    | "autopilot_test"
    | "autopilot_web_chat"
    | "autopilot_instagram"
    | "autopilot_telegram"
    | "paused_by_human"
    | "handoff_required";
  ai_state_updated_at: string | null;
  handoff_reason: string;
  unread_count: number;
  last_message_preview: string;
  can_send: boolean;
  provider_context: Record<string, unknown> & {
    state?:
      | "can_reply"
      | "waiting_for_customer"
      | "window_expired"
      | "human_agent_available"
      | "connection_expired"
      | "permission_missing"
      | "provider_degraded"
      | "provider_unavailable"
      | "organization_read_only"
      | "token_invalid"
      | "webhook_degraded"
      | "connection_paused"
      | "bot_blocked"
      | "user_not_started";
    professional_account?: string;
    connection_status?: string;
    connection_health?: string;
    human_agent_approved?: boolean;
    can_send?: boolean;
    human_agent_available?: boolean;
    last_customer_message_at?: string;
    standard_window_expires_at?: string;
    human_agent_window_expires_at?: string;
    bot_username?: string;
    bot_name?: string;
    webhook_status?: string;
    automation_mode?: "manual" | "suggest" | "autopilot";
    safe_chat_id?: string;
  };
  last_message_at: string | null;
  last_inbound_at: string | null;
  last_outbound_at: string | null;
  subject: string;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
};

export type ConversationMessage = {
  id: string;
  conversation: string;
  direction: "inbound" | "outbound" | "system";
  sender_type: "customer" | "agent" | "system" | "future_ai" | "ai";
  sender_membership: string | null;
  sender_name: string | null;
  provider_message_id: string | null;
  client_message_id: string | null;
  content_type: "text" | "note" | "event" | "media";
  body: string;
  status: "queued" | "sent" | "delivered" | "read" | "failed" | "received";
  error_code: string;
  reply_to: string | null;
  metadata: Record<string, unknown>;
  occurred_at: string;
  created_at: string;
  updated_at: string;
};

export type InstagramHealth = {
  status:
    | "draft"
    | "connected"
    | "degraded"
    | "expired"
    | "revoked"
    | "disconnected";
  account_connected: boolean;
  token_present: boolean;
  token_expired: boolean;
  token_expires_at: string | null;
  permissions_ok: boolean;
  missing_permissions: string[];
  webhook_subscription: string;
  last_webhook_at: string | null;
  last_send_at: string | null;
  last_health_check_at: string | null;
  error_code: string;
  graph_api_version: string;
  app_mode: "development" | "live";
  queue: { queued: number; failed: number; dead_letter: number };
};

export type InstagramConnection = {
  id: string;
  organization: string;
  channel_connection: string;
  instagram_user_id: string;
  username: string;
  account_type: string;
  profile_name: string;
  profile_picture_url: string;
  profile_picture_expires_at: string | null;
  graph_api_version: string;
  permission_snapshot: string[];
  webhook_subscription_status: string;
  connection_status: InstagramHealth["status"];
  automation_mode: "manual" | "suggest" | "autopilot";
  human_agent_approved: boolean;
  token_expires_at: string | null;
  last_webhook_at: string | null;
  last_successful_send_at: string | null;
  last_health_check_at: string | null;
  last_error_code: string;
  connected_at: string | null;
  disconnected_at: string | null;
  has_encrypted_token: boolean;
  health: InstagramHealth;
  app_review_checklist: Array<{ key: string; ready: boolean }>;
  created_at: string;
  updated_at: string;
};

export type CursorPaginated<T> = {
  next: string | null;
  previous: string | null;
  results: T[];
};

export type PipelineStage = {
  id: string;
  organization: string;
  pipeline: string;
  name: string;
  position: number;
  color_token: string;
  stage_type: "open" | "won" | "lost";
  is_active: boolean;
};

export type Pipeline = {
  id: string;
  organization: string;
  name: string;
  is_default: boolean;
  is_active: boolean;
  stages: PipelineStage[];
  created_at: string;
  updated_at: string;
};

export type Lead = {
  id: string;
  organization: string;
  contact: string;
  contact_name: string;
  source_conversation: string | null;
  source_channel_type: string;
  pipeline: string;
  pipeline_name: string;
  stage: string;
  stage_name: string;
  stage_type: "open" | "won" | "lost";
  title: string;
  description: string;
  assigned_membership: string | null;
  assigned_name: string | null;
  estimated_value: string | null;
  currency: string;
  status: "open" | "won" | "lost" | "archived";
  lost_reason: string;
  next_follow_up_at: string | null;
  created_at: string;
  updated_at: string;
  won_at: string | null;
  lost_at: string | null;
};

export type FollowUpTask = {
  id: string;
  organization: string;
  title: string;
  due_at: string;
  status: "open" | "completed" | "cancelled";
  assigned_membership: string | null;
  assigned_name: string | null;
  related_contact: string | null;
  contact_name: string | null;
  related_lead: string | null;
  lead_title: string | null;
  related_conversation: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CrmActivity = {
  id: string;
  event_type: string;
  actor_membership: string | null;
  actor_name: string | null;
  contact_id: string | null;
  conversation_id: string | null;
  lead_id: string | null;
  task_id: string | null;
  summary: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type CrmOverview = {
  open_conversations: number;
  unread_conversations: number;
  unassigned_conversations: number;
  active_contacts: number;
  open_leads: number;
  leads_by_stage: Array<{
    stage_id: string;
    stage__name: string;
    stage__color_token: string;
    count: number;
  }>;
  overdue_follow_ups: number;
  configured_channels: number;
  onboarding_completion_percentage: number;
  onboarding_completed_at: string | null;
  ai_context_status: "draft" | "published";
  ai_context_version: number;
};

export type AIRuntimeMode =
  | "off"
  | "suggest"
  | "autopilot_test"
  | "autopilot_web_chat"
  | "autopilot_instagram"
  | "autopilot_telegram";

export type AIRuntimeConfig = {
  id: string;
  organization: string;
  enabled: boolean;
  default_mode: AIRuntimeMode;
  provider: "fake" | "openai";
  model: string;
  max_output_tokens: number;
  max_tool_rounds: number;
  timeout_seconds: number;
  inbound_debounce_seconds: number;
  daily_run_limit: number;
  monthly_input_token_limit: number;
  monthly_output_token_limit: number;
  allowed_channel_connections: string[];
  published_context_version: number | null;
  provider_status:
    | "fake_ready"
    | "real_openai_disabled"
    | "openai_ready"
    | "openai_key_missing";
  real_openai_enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type AIToolPolicy = {
  id: string;
  organization: string;
  tool_name: string;
  description: string;
  mutating: boolean;
  enabled: boolean;
  execution_mode: "automatic" | "require_approval" | "disabled";
  configuration: Record<string, unknown>;
  version: number;
  updated_at: string;
};

export type AIToolCall = {
  id: string;
  tool_name: string;
  provider_call_id: string;
  input_redacted: Record<string, unknown>;
  output_redacted: Record<string, unknown>;
  status:
    | "proposed"
    | "awaiting_approval"
    | "approved"
    | "running"
    | "succeeded"
    | "failed"
    | "rejected"
    | "cancelled";
  requires_approval: boolean;
  approved_by: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  error_category: string;
  duration_ms: number;
  created_at: string;
  completed_at: string | null;
};

export type AIDraft = {
  id: string;
  run: string;
  conversation: string;
  body: string;
  language: string;
  status:
    | "pending"
    | "approved"
    | "edited_and_sent"
    | "rejected"
    | "expired"
    | "superseded";
  approved_by: string | null;
  approved_by_name: string | null;
  rejected_by: string | null;
  rejected_by_name: string | null;
  rejection_reason: string;
  created_at: string;
  updated_at: string;
  acted_at: string | null;
};

export type AIHandoff = {
  id: string;
  conversation: string;
  run: string | null;
  reason_code: string;
  safe_summary: string;
  requested_by: "customer" | "ai" | "policy" | "system";
  status: "open" | "acknowledged" | "resolved";
  assigned_membership: string | null;
  assigned_name: string | null;
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
};

export type AIRun = {
  id: string;
  conversation: string;
  trigger_message: string;
  status:
    | "queued"
    | "running"
    | "waiting_for_approval"
    | "completed"
    | "handoff"
    | "failed"
    | "cancelled"
    | "superseded";
  mode: AIRuntimeMode;
  provider: "fake" | "openai";
  model: string;
  ai_context_revision: string;
  prompt_template_version: string;
  prompt_hash: string;
  response_id: string;
  provider_request_id: string;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  latency_ms: number;
  tool_rounds: number;
  outcome:
    | ""
    | "draft"
    | "sent_test_reply"
    | "sent_web_chat_reply"
    | "sent_instagram_reply"
    | "sent_telegram_reply"
    | "handoff"
    | "no_reply"
    | "failed";
  response_language: string;
  error_category: string;
  error_code: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  tool_calls: AIToolCall[];
  draft: AIDraft | null;
  handoffs: AIHandoff[];
};

export type AIUsage = {
  date: string;
  month: string;
  daily_runs: number;
  monthly_input_tokens: number;
  monthly_output_tokens: number;
  monthly_cached_tokens: number;
  status_counts: Record<string, number>;
  outcome_counts: Record<string, number>;
  draft_status_counts: Record<string, number>;
  tool_status_counts: Record<string, number>;
  average_provider_latency_ms: number;
  handoff_rate: number;
  stale_run_cancellations: number;
};

export type TelegramReadiness = {
  enabled: boolean;
  fake_provider: boolean;
  ready: boolean;
  status: string;
  can_manage_bots: boolean;
  manager_username: string;
  reachable?: boolean;
};

export type TelegramUserLink = {
  id?: string;
  telegram_user_id?: number | null;
  telegram_username?: string;
  status: "not_linked" | "pending" | "linked" | "expired" | "revoked";
  expires_at?: string;
  linked_at?: string | null;
  created_at?: string;
  telegram_url?: string;
};

export type TelegramManagedBotRequest = {
  id: string;
  organization: string;
  linked_telegram_user_id: number;
  suggested_username: string;
  suggested_name: string;
  status: string;
  expires_at: string;
  created_bot_user_id: number | null;
  created_bot_username: string;
  error_code: string;
  created_at: string;
  updated_at: string;
};

export type TelegramHealth = {
  status: string;
  webhook_status: string;
  has_encrypted_token: boolean;
  provider_reachable: boolean | null;
  bot_matches: boolean | null;
  webhook_matches: boolean | null;
  pending_updates: number | null;
  last_error_code: string;
};

export type TelegramBotConnection = {
  id: string;
  organization: string;
  channel_connection: string;
  connection_type: "managed" | "existing";
  bot_user_id: number;
  bot_username: string;
  bot_name: string;
  owner_telegram_user_id: number | null;
  status: string;
  token_version: number;
  webhook_status: string;
  allowed_updates: string[];
  access_restricted: boolean;
  permitted_telegram_user_ids: number[];
  default_language: Locale;
  supported_languages: Locale[];
  privacy_url: string;
  automation_mode: "manual" | "suggest" | "autopilot";
  last_update_at: string | null;
  last_send_at: string | null;
  last_health_check_at: string | null;
  last_error_code: string;
  connected_at: string | null;
  disconnected_at: string | null;
  has_encrypted_token: boolean;
  health: TelegramHealth;
  customer_start_url: string;
  created_at: string;
  updated_at: string;
};

export type WebChatInstallation = {
  id: string;
  organization: string;
  channel_connection: string;
  public_key: string;
  status: "draft" | "active" | "paused" | "revoked";
  display_name: string;
  assistant_label: string;
  greeting: string;
  offline_message: string;
  human_handoff_message: string;
  privacy_policy_url: string;
  terms_url: string;
  consent_text: string;
  consent_version: string;
  require_consent: boolean;
  require_prechat_form: boolean;
  collect_name: boolean;
  collect_email: boolean;
  collect_phone: boolean;
  default_language: Locale;
  supported_languages: Locale[];
  default_branch: string | null;
  allowed_origins: string[];
  theme_config: { accent: string; position: string; radius: string };
  ai_mode: "manual" | "suggest" | "autopilot";
  retention_days: number;
  production_approved: boolean;
  live_ai_opt_in: boolean;
  health: {
    status: string;
    origin_count: number;
    published_context: boolean;
    public_api_enabled: boolean;
    last_session_at: string | null;
  };
  session_counts: { total: number; active: number; blocked: number };
  embed_snippet: string;
  created_at: string;
  updated_at: string;
};

export type WebChatSessionSummary = {
  public_session_id: string;
  status: string;
  language: Locale;
  origin: string;
  conversation: string | null;
  contact: string | null;
  consented_at: string | null;
  started_at: string;
  last_seen_at: string;
  expires_at: string;
  closed_at: string | null;
  abuse_score: number;
  first_message_at: string | null;
  first_response_at: string | null;
};
