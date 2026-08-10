import type { Conversation } from "@workspace/api-client";
import { describe, expect, it } from "vitest";
import {
  canManageCrm,
  canMoveLeadToStage,
  composerState,
  crmQueryKeys,
  filterConversations,
  taskBucket,
} from "@/lib/crm";

function conversation(overrides: Partial<Conversation> = {}): Conversation {
  return {
    id: "conversation-1",
    organization: "org-a",
    channel_connection: "channel-a",
    channel_type: "webchat",
    channel_name: "Development test channel",
    channel_provider: "internal_test",
    external_thread_id: "thread-a",
    contact: "contact-a",
    contact_name: "Test customer",
    contact_status: "active",
    status: "open",
    priority: "normal",
    assignment_state: "unassigned",
    assigned_membership: null,
    assigned_name: null,
    automation_state: "manual",
    ai_state: "off",
    ai_state_updated_at: null,
    handoff_reason: "",
    unread_count: 1,
    last_message_preview: "Hello",
    can_send: true,
    last_message_at: "2026-08-10T10:00:00Z",
    last_inbound_at: "2026-08-10T10:00:00Z",
    last_outbound_at: null,
    subject: "",
    created_at: "2026-08-10T10:00:00Z",
    updated_at: "2026-08-10T10:00:00Z",
    resolved_at: null,
    ...overrides,
  };
}

describe("CRM tenant and role state", () => {
  it("includes the organization in every CRM query key", () => {
    expect(crmQueryKeys.messages("org-a", "conversation-1")).not.toEqual(
      crmQueryKeys.messages("org-b", "conversation-1"),
    );
    expect(crmQueryKeys.root("org-a")).toEqual(["crm", "org-a"]);
  });

  it("filters inbox rows without mutating the tenant result", () => {
    const rows = [
      conversation(),
      conversation({
        id: "conversation-2",
        unread_count: 0,
        assignment_state: "assigned",
      }),
    ];
    expect(filterConversations(rows, "unread")).toHaveLength(1);
    expect(filterConversations(rows, "unassigned")).toHaveLength(1);
    expect(filterConversations(rows, "all")).toHaveLength(2);
    expect(rows).toHaveLength(2);
  });

  it("enables only internal test composition and preserves note-capable roles", () => {
    expect(composerState(conversation(), "agent", "active")).toBe("enabled");
    expect(
      composerState(conversation({ can_send: false }), "agent", "active"),
    ).toBe("provider_unavailable");
    expect(composerState(conversation(), "viewer", "active")).toBe(
      "permission_denied",
    );
    expect(composerState(conversation(), "owner", "suspended")).toBe(
      "read_only",
    );
  });

  it("keeps merge, pipeline, and lead moves behind explicit validation", () => {
    expect(canManageCrm("manager")).toBe(true);
    expect(canManageCrm("agent")).toBe(false);
    expect(canMoveLeadToStage("pipeline-a", "pipeline-a")).toBe(true);
    expect(canMoveLeadToStage("pipeline-a", "pipeline-b")).toBe(false);
  });

  it("groups tasks into truthful due-date buckets", () => {
    expect(
      taskBucket(new Date(Date.now() - 60_000).toISOString(), "open"),
    ).toBe("overdue");
    expect(
      taskBucket(new Date(Date.now() + 86_400_000).toISOString(), "open"),
    ).toBe("upcoming");
    expect(taskBucket(new Date().toISOString(), "completed")).toBe("completed");
  });
});
