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
