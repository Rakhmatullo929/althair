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
  handoff_reason: string;
  unread_count: number;
  last_message_preview: string;
  can_send: boolean;
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
  sender_type: "customer" | "agent" | "system" | "future_ai";
  sender_membership: string | null;
  sender_name: string | null;
  provider_message_id: string | null;
  client_message_id: string | null;
  content_type: "text" | "note" | "event";
  body: string;
  status: "queued" | "sent" | "delivered" | "failed" | "received";
  error_code: string;
  reply_to: string | null;
  metadata: Record<string, unknown>;
  occurred_at: string;
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
