import type { Conversation, OrganizationRole } from "@workspace/api-client";
import { instagramComposerState } from "./instagram";
import { telegramComposerState } from "./telegram";
import { gmailComposerState } from "./gmail";
import { smsComposerState } from "./sms";

export const crmQueryKeys = {
  root: (organizationId: string) => ["crm", organizationId] as const,
  overview: (organizationId: string) =>
    [...crmQueryKeys.root(organizationId), "overview"] as const,
  conversations: (organizationId: string, filters: Record<string, unknown>) =>
    [...crmQueryKeys.root(organizationId), "conversations", filters] as const,
  messages: (organizationId: string, conversationId: string) =>
    [
      ...crmQueryKeys.root(organizationId),
      "conversations",
      conversationId,
      "messages",
    ] as const,
  contacts: (organizationId: string, search = "") =>
    [...crmQueryKeys.root(organizationId), "contacts", search] as const,
  contact: (organizationId: string, contactId: string) =>
    [...crmQueryKeys.root(organizationId), "contacts", contactId] as const,
  leads: (organizationId: string, pipelineId = "all") =>
    [...crmQueryKeys.root(organizationId), "leads", pipelineId] as const,
  pipelines: (organizationId: string) =>
    [...crmQueryKeys.root(organizationId), "pipelines"] as const,
  tasks: (organizationId: string, status = "all") =>
    [...crmQueryKeys.root(organizationId), "tasks", status] as const,
  activity: (organizationId: string) =>
    [...crmQueryKeys.root(organizationId), "activity"] as const,
};

export function canOperateCrm(role: OrganizationRole) {
  return role !== "viewer";
}

export function canManageCrm(role: OrganizationRole) {
  return ["owner", "admin", "manager"].includes(role);
}

export function canCreateTestConversation(role: OrganizationRole) {
  return role === "owner" || role === "admin";
}

export function composerState(
  conversation: Conversation,
  role: OrganizationRole,
  organizationStatus: string,
) {
  if (organizationStatus === "suspended" || organizationStatus === "archived")
    return "read_only" as const;
  if (!canOperateCrm(role)) return "permission_denied" as const;
  const instagramState = instagramComposerState(conversation);
  if (instagramState) return instagramState;
  const telegramState = telegramComposerState(conversation);
  if (telegramState) return telegramState;
  const gmailState = gmailComposerState(conversation);
  if (gmailState) return gmailState;
  const smsState = smsComposerState(conversation);
  if (smsState) return smsState;
  if (!conversation.can_send) return "provider_unavailable" as const;
  return "enabled" as const;
}

export function filterConversations(
  conversations: Conversation[],
  filter: "all" | "unread" | "unassigned",
) {
  if (filter === "unread")
    return conversations.filter(
      (conversation) => conversation.unread_count > 0,
    );
  if (filter === "unassigned")
    return conversations.filter(
      (conversation) => conversation.assignment_state === "unassigned",
    );
  return conversations;
}

export function relativeTime(value: string | null, locale: string) {
  if (!value) return "—";
  const delta = new Date(value).getTime() - Date.now();
  const minutes = Math.round(delta / 60_000);
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (Math.abs(minutes) < 60) return formatter.format(minutes, "minute");
  const hours = Math.round(minutes / 60);
  if (Math.abs(hours) < 24) return formatter.format(hours, "hour");
  return formatter.format(Math.round(hours / 24), "day");
}

export function taskBucket(dueAt: string, status: string) {
  if (status === "completed") return "completed" as const;
  const due = new Date(dueAt);
  const now = new Date();
  if (due.getTime() < now.getTime()) return "overdue" as const;
  const endToday = new Date(now);
  endToday.setHours(23, 59, 59, 999);
  if (due.getTime() <= endToday.getTime()) return "today" as const;
  return "upcoming" as const;
}

export function canMoveLeadToStage(
  leadPipelineId: string,
  stagePipelineId: string,
) {
  return Boolean(leadPipelineId) && leadPipelineId === stagePipelineId;
}
