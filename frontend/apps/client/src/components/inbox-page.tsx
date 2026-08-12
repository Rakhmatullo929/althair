"use client";

import type { Conversation, ConversationMessage } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AtSign,
  ArrowLeft,
  BotOff,
  CheckCheck,
  CircleAlert,
  ExternalLink,
  Inbox,
  ListPlus,
  Mail,
  MessageSquareText,
  NotebookPen,
  Paperclip,
  Plus,
  RotateCcw,
  Search,
  Send,
  Smartphone,
  UserCheck,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import {
  canCreateTestConversation,
  canManageCrm,
  canOperateCrm,
  composerState,
  crmQueryKeys,
  relativeTime,
} from "@/lib/crm";
import { estimateSMSSegments } from "@/lib/sms";
import { useWorkspace } from "./workspace-provider";
import { CrmDialog, formatDateTime, PlainText } from "./crm-shared";
import { ConversationAIPanel } from "./conversation-ai-panel";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

type InboxFilter = "all" | "unread" | "unassigned" | "mine";

function Timeline({
  messages,
  locale,
  aiLabel,
  statusLabel,
}: {
  messages: ConversationMessage[];
  locale: string;
  aiLabel: string;
  statusLabel: (status: string) => string;
}) {
  const ordered = [...messages].reverse();
  return (
    <ol className="message-timeline" aria-live="polite">
      {ordered.map((message, index) => {
        const day = new Intl.DateTimeFormat(locale, {
          dateStyle: "long",
        }).format(new Date(message.occurred_at));
        const previous = ordered[index - 1];
        const previousDay = previous
          ? new Intl.DateTimeFormat(locale, { dateStyle: "long" }).format(
              new Date(previous.occurred_at),
            )
          : "";
        const separator = day !== previousDay;
        return (
          <li
            key={message.id}
            className={`message-row ${message.direction} type-${message.content_type}`}
          >
            {separator ? (
              <div className="day-separator">
                <span>{day}</span>
              </div>
            ) : null}
            <article className="message-bubble">
              <header>
                <strong>{message.sender_name}</strong>
                <time dateTime={message.occurred_at}>
                  {formatDateTime(message.occurred_at, locale)}
                </time>
              </header>
              <PlainText value={message.body} />
              {Array.isArray(message.metadata.attachments) &&
              message.metadata.attachments.length ? (
                <div className="gmail-attachment-list">
                  <Paperclip aria-hidden="true" />
                  {message.metadata.attachments.map((item, attachmentIndex) => {
                    const attachment = item as {
                      filename?: string;
                      size?: number;
                      download_path?: string;
                    };
                    return attachment.download_path ? (
                      <a
                        key={`${message.id}-${attachmentIndex}`}
                        href={`/api/v1${attachment.download_path}`}
                      >
                        {attachment.filename || "Attachment"}
                      </a>
                    ) : (
                      <span key={`${message.id}-${attachmentIndex}`}>
                        {attachment.filename || "Attachment"}
                      </span>
                    );
                  })}
                </div>
              ) : null}
              {message.metadata.ai_generated ? (
                <em className="ai-message-label">
                  <BotOff aria-hidden="true" />
                  {aiLabel}
                </em>
              ) : null}
              <small>
                {statusLabel(
                  message.metadata.provider === "sms" &&
                    message.status === "read"
                    ? "sent"
                    : message.status,
                )}
              </small>
            </article>
          </li>
        );
      })}
    </ol>
  );
}

export function InboxPage() {
  const t = useTranslations("crm");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const membership = workspace.membership!;
  const [filter, setFilter] = useState<InboxFilter>("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [noteMode, setNoteMode] = useState(false);
  const [draft, setDraft] = useState("");
  const [ccDraft, setCcDraft] = useState("");
  const [useHumanAgent, setUseHumanAgent] = useState(false);
  const [confirmSmsSegments, setConfirmSmsSegments] = useState(false);
  const [testOpen, setTestOpen] = useState(false);
  const [testName, setTestName] = useState("Test customer");
  const [testBody, setTestBody] = useState(
    "Hello, I would like to speak with your team.",
  );
  const [notice, setNotice] = useState("");
  const readOnly = ["suspended", "archived"].includes(
    membership.organization_status,
  );
  const filters = useMemo(
    () => ({
      search,
      unread: filter === "unread" || undefined,
      unassigned: filter === "unassigned" || undefined,
      assigned_to_me: filter === "mine" || undefined,
    }),
    [filter, search],
  );
  const conversations = useQuery({
    queryKey: crmQueryKeys.conversations(organizationId, filters),
    queryFn: () => workspace.api.conversations(filters),
    refetchInterval: () =>
      typeof document !== "undefined" && document.visibilityState === "visible"
        ? 5000
        : false,
  });
  const conversationRows = conversations.data?.results ?? [];
  const selected =
    selectedId === null
      ? null
      : (conversationRows.find((item) => item.id === selectedId) ??
        conversationRows[0] ??
        null);
  const activeConversationId = selected?.id ?? null;
  const messages = useQuery({
    queryKey: crmQueryKeys.messages(
      organizationId,
      activeConversationId ?? "none",
    ),
    queryFn: () => workspace.api.conversationMessages(activeConversationId!),
    enabled: Boolean(activeConversationId),
    refetchInterval: () =>
      typeof document !== "undefined" && document.visibilityState === "visible"
        ? 5000
        : false,
  });
  const contact = useQuery({
    queryKey: crmQueryKeys.contact(organizationId, selected?.contact ?? "none"),
    queryFn: () => workspace.api.contact(selected!.contact),
    enabled: Boolean(selected),
  });
  const members = useQuery({
    queryKey: [...crmQueryKeys.root(organizationId), "members"],
    queryFn: () => workspace.api.memberships(organizationId),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: crmQueryKeys.root(organizationId),
    });
  };
  const mutation = useMutation({
    mutationFn: async (action: () => Promise<unknown>) => action(),
    onSuccess: async () => {
      setNotice(t("common.saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const testMutation = useMutation({
    mutationFn: () =>
      workspace.api.createTestConversation({
        display_name: testName,
        body: testBody,
      }),
    onSuccess: async (conversation) => {
      setTestOpen(false);
      setSelectedId(conversation.id);
      setNotice(t("inbox.testCreated"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const submitComposer = () => {
    if (!selected || !draft.trim()) return;
    const body = draft.trim();
    const cc = ccDraft
      .split(/[\s,;]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    setDraft("");
    setCcDraft("");
    const confirmsSMS = selected.channel_type === "sms" && confirmSmsSegments;
    setConfirmSmsSegments(false);
    mutation.mutate(() =>
      noteMode
        ? workspace.api.addConversationNote(selected.id, body)
        : workspace.api.sendMessage(
            selected.id,
            body,
            crypto.randomUUID(),
            useHumanAgent,
            cc,
            confirmsSMS,
          ),
    );
  };
  const state = selected
    ? composerState(selected, membership.role, membership.organization_status)
    : "permission_denied";
  const smsEstimate = useMemo(() => estimateSMSSegments(draft), [draft]);
  const smsConfirmationThreshold =
    selected?.provider_context.confirm_above_segments ?? 3;
  const smsMaxSegments = selected?.provider_context.max_segments ?? 10;

  return (
    <>
      <PageHeading
        title={t("inbox.title")}
        description={t("inbox.description")}
        actions={
          canCreateTestConversation(membership.role) && !readOnly ? (
            <button
              className="button secondary"
              type="button"
              onClick={() => setTestOpen(true)}
            >
              <Plus aria-hidden="true" /> {t("inbox.newTest")}
            </button>
          ) : undefined
        }
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      <section className={`inbox-layout ${selected ? "has-selection" : ""}`}>
        <aside
          className="conversation-list-panel"
          aria-label={t("inbox.listLabel")}
        >
          <div className="inbox-search">
            <Search aria-hidden="true" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t("inbox.search")}
              aria-label={t("inbox.search")}
            />
          </div>
          <div
            className="segmented-filter"
            role="group"
            aria-label={t("inbox.filtersLabel")}
          >
            {(["all", "unread", "unassigned", "mine"] as InboxFilter[]).map(
              (item) => (
                <button
                  key={item}
                  type="button"
                  className={filter === item ? "active" : ""}
                  onClick={() => setFilter(item)}
                >
                  {t(`inbox.filters.${item}`)}
                </button>
              ),
            )}
          </div>
          {conversations.isLoading ? (
            <PageSkeleton />
          ) : conversations.error ? (
            <ErrorState
              title={t("common.error")}
              description={(conversations.error as Error).message}
              onRetry={() => void conversations.refetch()}
            />
          ) : conversations.data?.results.length ? (
            <ul className="conversation-list">
              {conversations.data.results.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    type="button"
                    className={
                      activeConversationId === conversation.id ? "active" : ""
                    }
                    onClick={() => setSelectedId(conversation.id)}
                  >
                    <span className="conversation-avatar">
                      {conversation.contact_name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className="conversation-copy">
                      <span>
                        <strong>{conversation.contact_name}</strong>
                        <time>
                          {relativeTime(conversation.last_message_at, locale)}
                        </time>
                      </span>
                      <small>
                        {conversation.channel_name} ·{" "}
                        {conversation.assigned_name ?? t("status.unassigned")}
                      </small>
                      <p>
                        {conversation.last_message_preview ||
                          t("inbox.noPreview")}
                      </p>
                    </span>
                    {conversation.unread_count ? (
                      <span
                        className="unread-pill"
                        aria-label={t("inbox.unreadCount", {
                          count: conversation.unread_count,
                        })}
                      >
                        {conversation.unread_count}
                      </span>
                    ) : null}
                    <span
                      className={`priority-dot priority-${conversation.priority}`}
                      title={t(`priority.${conversation.priority}`)}
                    />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              icon={<Inbox />}
              title={t("inbox.emptyTitle")}
              description={t("inbox.emptyDescription")}
            />
          )}
        </aside>
        <section
          className="conversation-panel"
          aria-label={t("inbox.conversationLabel")}
        >
          {selected ? (
            <>
              <header className="conversation-header">
                <button
                  className="back-button mobile-only"
                  type="button"
                  onClick={() => setSelectedId(null)}
                >
                  <ArrowLeft aria-hidden="true" /> {t("common.back")}
                </button>
                <div>
                  <h2>{selected.contact_name}</h2>
                  <p>
                    {selected.channel_name} ·{" "}
                    <StatusBadge status={selected.status} />
                  </p>
                </div>
                <div className="conversation-actions">
                  {selected.unread_count ? (
                    <button
                      className="button secondary"
                      disabled={readOnly}
                      onClick={() =>
                        mutation.mutate(() =>
                          workspace.api.markConversationRead(selected.id),
                        )
                      }
                    >
                      <CheckCheck />
                      {t("inbox.markRead")}
                    </button>
                  ) : null}
                  <button
                    className="button secondary"
                    disabled={readOnly || !canOperateCrm(membership.role)}
                    onClick={() =>
                      mutation.mutate(() =>
                        selected.status === "resolved"
                          ? workspace.api.reopenConversation(selected.id)
                          : workspace.api.resolveConversation(selected.id),
                      )
                    }
                  >
                    {selected.status === "resolved" ? (
                      <RotateCcw />
                    ) : (
                      <CheckCheck />
                    )}
                    {selected.status === "resolved"
                      ? t("inbox.reopen")
                      : t("inbox.resolve")}
                  </button>
                </div>
              </header>
              <div className="conversation-controls">
                <label>
                  {t("inbox.assignment")}
                  <select
                    value={selected.assigned_membership ?? ""}
                    disabled={readOnly || !canOperateCrm(membership.role)}
                    onChange={(event) => {
                      const membershipId = event.target.value || null;
                      mutation.mutate(() =>
                        workspace.api.assignConversation(
                          selected.id,
                          membershipId,
                        ),
                      );
                    }}
                  >
                    <option value="">{t("status.unassigned")}</option>
                    {members.data?.results
                      .filter((item) => item.status === "active")
                      .map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.user_name || item.user_email}
                        </option>
                      ))}
                  </select>
                </label>
                <label>
                  {t("inbox.priority")}
                  <select
                    value={selected.priority}
                    disabled={readOnly || !canOperateCrm(membership.role)}
                    onChange={(event) => {
                      const priority = event.target
                        .value as Conversation["priority"];
                      mutation.mutate(() =>
                        workspace.api.updateConversation(selected.id, {
                          priority,
                        }),
                      );
                    }}
                  >
                    {(["low", "normal", "high", "urgent"] as const).map(
                      (item) => (
                        <option value={item} key={item}>
                          {t(`priority.${item}`)}
                        </option>
                      ),
                    )}
                  </select>
                </label>
                <label>
                  {t("inbox.handoff")}
                  <select
                    value={selected.automation_state}
                    disabled={readOnly || !canOperateCrm(membership.role)}
                    onChange={(event) => {
                      const automationState = event.target
                        .value as Conversation["automation_state"];
                      mutation.mutate(() =>
                        workspace.api.updateConversation(selected.id, {
                          automation_state: automationState,
                        }),
                      );
                    }}
                  >
                    <option value="manual">{t("automation.manual")}</option>
                    <option value="ai_paused">
                      {t("automation.aiPaused")}
                    </option>
                    <option value="ai_available">
                      {t("automation.aiAvailable")}
                    </option>
                  </select>
                </label>
              </div>
              {selected.channel_type === "instagram" ? (
                <div
                  className={`instagram-inbox-policy state-${selected.provider_context.state ?? "unknown"}`}
                  role="status"
                >
                  <AtSign aria-hidden="true" />
                  <div>
                    <strong>
                      {selected.provider_context.professional_account ??
                        selected.channel_name}
                    </strong>
                    <p>
                      {t(
                        `instagramStates.${selected.provider_context.state ?? "provider_unavailable"}`,
                      )}
                    </p>
                    {selected.provider_context.standard_window_expires_at ? (
                      <small>
                        {t("inbox.instagramWindow", {
                          time: relativeTime(
                            selected.provider_context
                              .standard_window_expires_at,
                            locale,
                          ),
                        })}
                      </small>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {selected.channel_type === "telegram" ? (
                <div
                  className={`instagram-inbox-policy telegram-inbox-policy state-${selected.provider_context.state ?? "provider_unavailable"}`}
                  role="status"
                >
                  <Send aria-hidden="true" />
                  <div>
                    <strong>
                      {selected.provider_context.bot_username
                        ? `@${selected.provider_context.bot_username}`
                        : selected.channel_name}
                    </strong>
                    <p>
                      {t(
                        `telegramStates.${selected.provider_context.state ?? "provider_unavailable"}`,
                      )}
                    </p>
                  </div>
                </div>
              ) : null}
              {selected.channel_type === "gmail" ? (
                <div
                  className={`instagram-inbox-policy gmail-inbox-policy state-${selected.provider_context.state ?? "provider_unavailable"}`}
                  role="status"
                >
                  <Mail aria-hidden="true" />
                  <div>
                    <strong>
                      {selected.provider_context.mailbox ??
                        selected.channel_name}
                    </strong>
                    <p>
                      {t(
                        `gmailStates.${selected.provider_context.state ?? "provider_unavailable"}`,
                      )}
                    </p>
                    {selected.subject ? (
                      <small>{selected.subject}</small>
                    ) : null}
                    {selected.provider_context.participants?.length ? (
                      <small>
                        {t("inbox.gmailParticipants")}:{" "}
                        {selected.provider_context.participants.join(", ")}
                      </small>
                    ) : null}
                    {selected.provider_context.labels?.length ? (
                      <small>
                        {t("inbox.gmailLabels")}:{" "}
                        {selected.provider_context.labels.join(", ")}
                      </small>
                    ) : null}
                    {selected.provider_context.has_attachments ? (
                      <small>
                        <Paperclip aria-hidden="true" />{" "}
                        {t("inbox.gmailAttachments")}
                      </small>
                    ) : null}
                    {selected.provider_context.thread_url ? (
                      <a
                        href={selected.provider_context.thread_url}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        {t("inbox.openGmailThread")}{" "}
                        <ExternalLink aria-hidden="true" />
                      </a>
                    ) : null}
                  </div>
                </div>
              ) : null}
              {selected.channel_type === "sms" ? (
                <div
                  className={`instagram-inbox-policy sms-inbox-policy state-${selected.provider_context.state ?? "provider_unavailable"}`}
                  role="status"
                >
                  <Smartphone aria-hidden="true" />
                  <div>
                    <strong>
                      {selected.provider_context.sender_address ??
                        selected.channel_name}
                    </strong>
                    <p>
                      {t(
                        `smsStates.${selected.provider_context.state ?? "provider_unavailable"}`,
                      )}
                    </p>
                    <small>
                      {t("inbox.smsConsent")}:{" "}
                      {t(
                        `smsConsent.${selected.provider_context.consent_state ?? "unknown"}`,
                      )}
                    </small>
                    <small>{t("inbox.smsNoRead")}</small>
                    {selected.provider_context.sms_connection_id &&
                    selected.provider_context.consent_state !== "blocked" &&
                    canOperateCrm(membership.role) &&
                    !readOnly ? (
                      <button
                        className="button secondary sms-block-button"
                        type="button"
                        onClick={() =>
                          mutation.mutate(() =>
                            workspace.api.updateSMSConsent(
                              selected.provider_context.sms_connection_id!,
                              selected.contact,
                              "blocked",
                            ),
                          )
                        }
                      >
                        {t("inbox.smsBlock")}
                      </button>
                    ) : null}
                  </div>
                </div>
              ) : null}
              <ConversationAIPanel
                conversation={selected}
                organizationId={organizationId}
                role={membership.role}
                readOnly={readOnly}
                onRefresh={refresh}
              />
              <div className="timeline-scroll">
                {messages.isLoading ? (
                  <PageSkeleton />
                ) : messages.data?.results.length ? (
                  <Timeline
                    messages={messages.data.results}
                    locale={locale}
                    aiLabel={t("inbox.aiGenerated")}
                    statusLabel={(status) => t(`messageStatus.${status}`)}
                  />
                ) : (
                  <EmptyState
                    icon={<MessageSquareText />}
                    title={t("inbox.noMessages")}
                    description={t("inbox.noMessagesHint")}
                  />
                )}
              </div>
              <div className="composer">
                <div
                  className="composer-tabs"
                  role="tablist"
                  aria-label={t("inbox.composerMode")}
                >
                  <button
                    role="tab"
                    aria-selected={!noteMode}
                    className={!noteMode ? "active" : ""}
                    onClick={() => setNoteMode(false)}
                  >
                    <Send />
                    {t("inbox.reply")}
                  </button>
                  <button
                    role="tab"
                    aria-selected={noteMode}
                    className={noteMode ? "active" : ""}
                    onClick={() => setNoteMode(true)}
                  >
                    <NotebookPen />
                    {t("inbox.internalNote")}
                  </button>
                </div>
                {!noteMode && state !== "enabled" ? (
                  <div className="composer-warning" role="status">
                    {state === "provider_unavailable" ? (
                      <BotOff />
                    ) : (
                      <CircleAlert />
                    )}
                    {t(`composer.${state}`)}
                  </div>
                ) : null}
                {!noteMode && state === "human_agent_available" ? (
                  <label className="human-agent-toggle">
                    <input
                      type="checkbox"
                      checked={useHumanAgent}
                      onChange={(event) =>
                        setUseHumanAgent(event.target.checked)
                      }
                    />
                    <span>
                      <strong>{t("inbox.humanAgentSend")}</strong>
                      <small>{t("inbox.humanAgentSendHint")}</small>
                    </span>
                  </label>
                ) : null}
                {!noteMode && selected.channel_type === "gmail" ? (
                  <label className="gmail-cc-field">
                    <span>{t("inbox.gmailCc")}</span>
                    <input
                      type="text"
                      value={ccDraft}
                      onChange={(event) => setCcDraft(event.target.value)}
                      placeholder={t("inbox.gmailCcPlaceholder")}
                      disabled={readOnly || state !== "enabled"}
                    />
                  </label>
                ) : null}
                {!noteMode && selected.channel_type === "sms" ? (
                  <div className="sms-segment-meter" role="status">
                    <span>
                      {t("inbox.smsEncoding")}:{" "}
                      <strong>{smsEstimate.encoding}</strong>
                    </span>
                    <span>
                      {t("inbox.smsSegments", { count: smsEstimate.segments })}
                    </span>
                    {smsEstimate.segments > smsMaxSegments ? (
                      <strong>{t("inbox.smsTooLong")}</strong>
                    ) : null}
                    {smsEstimate.segments > smsConfirmationThreshold &&
                    smsEstimate.segments <= smsMaxSegments ? (
                      <label>
                        <input
                          type="checkbox"
                          checked={confirmSmsSegments}
                          onChange={(event) =>
                            setConfirmSmsSegments(event.target.checked)
                          }
                        />
                        {t("inbox.smsConfirmSegments")}
                      </label>
                    ) : null}
                  </div>
                ) : null}
                <textarea
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  placeholder={
                    noteMode
                      ? t("inbox.notePlaceholder")
                      : t("inbox.replyPlaceholder")
                  }
                  disabled={
                    readOnly ||
                    !canOperateCrm(membership.role) ||
                    (!noteMode &&
                      state !== "enabled" &&
                      !(state === "human_agent_available" && useHumanAgent))
                  }
                  rows={3}
                />
                <button
                  className="button primary"
                  type="button"
                  disabled={
                    !draft.trim() ||
                    mutation.isPending ||
                    readOnly ||
                    (!noteMode &&
                      state !== "enabled" &&
                      !(state === "human_agent_available" && useHumanAgent)) ||
                    (!noteMode &&
                      selected.channel_type === "sms" &&
                      smsEstimate.segments > smsMaxSegments) ||
                    (!noteMode &&
                      selected.channel_type === "sms" &&
                      smsEstimate.segments > smsConfirmationThreshold &&
                      !confirmSmsSegments)
                  }
                  onClick={submitComposer}
                >
                  {noteMode ? <NotebookPen /> : <Send />}
                  {noteMode ? t("inbox.addNote") : t("inbox.send")}
                </button>
              </div>
            </>
          ) : (
            <EmptyState
              icon={<MessageSquareText />}
              title={t("inbox.selectTitle")}
              description={t("inbox.selectDescription")}
            />
          )}
        </section>
        <aside
          className="contact-context-panel"
          aria-label={t("inbox.contextLabel")}
        >
          {selected && contact.data ? (
            <>
              <header>
                <span className="conversation-avatar large">
                  {contact.data.display_name.slice(0, 1)}
                </span>
                <div>
                  <h2>{contact.data.display_name}</h2>
                  <p>{contact.data.company_name || t("contacts.noCompany")}</p>
                </div>
              </header>
              <section>
                <h3>{t("contacts.identities")}</h3>
                {contact.data.identities.length ? (
                  <ul className="identity-list">
                    {contact.data.identities.map((identity) => (
                      <li key={identity.id}>
                        <span>{t(`identity.${identity.type}`)}</span>
                        <strong>{identity.raw_value}</strong>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted-copy">{t("contacts.noIdentities")}</p>
                )}
              </section>
              <section>
                <h3>{t("contacts.notes")}</h3>
                <p className="plain-summary">
                  {contact.data.notes_summary || t("contacts.noNotes")}
                </p>
              </section>
              <section className="context-actions">
                {canManageCrm(membership.role) && !readOnly ? (
                  <button
                    className="button primary"
                    onClick={() =>
                      mutation.mutate(() =>
                        workspace.api.createLead({
                          contact: selected.contact,
                          source_conversation: selected.id,
                          title: `${t("leads.inquiryFrom")} ${selected.contact_name}`,
                        }),
                      )
                    }
                  >
                    <ListPlus />
                    {t("inbox.createLead")}
                  </button>
                ) : null}
                {canManageCrm(membership.role) && !readOnly ? (
                  <button
                    className="button secondary"
                    onClick={() =>
                      mutation.mutate(() =>
                        workspace.api.createFollowUpTask({
                          title: t("tasks.followUpWith", {
                            name: selected.contact_name,
                          }),
                          due_at: new Date(
                            Date.now() + 86_400_000,
                          ).toISOString(),
                          related_contact: selected.contact,
                          related_conversation: selected.id,
                          assigned_membership: membership.id,
                        }),
                      )
                    }
                  >
                    <UserCheck />
                    {t("inbox.createTask")}
                  </button>
                ) : null}
              </section>
              <small className="honest-ai-note">
                <BotOff />
                {t("inbox.noAiSummary")}
              </small>
            </>
          ) : null}
        </aside>
      </section>
      {testOpen ? (
        <CrmDialog
          title={t("inbox.testTitle")}
          description={t("inbox.testDescription")}
          closeLabel={t("common.close")}
          onClose={() => setTestOpen(false)}
        >
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              testMutation.mutate();
            }}
          >
            <label className="field">
              {t("contacts.displayName")}
              <input
                autoFocus
                value={testName}
                onChange={(event) => setTestName(event.target.value)}
                required
                maxLength={200}
              />
            </label>
            <label className="field">
              {t("inbox.inboundMessage")}
              <textarea
                value={testBody}
                onChange={(event) => setTestBody(event.target.value)}
                required
                maxLength={10000}
                rows={5}
              />
            </label>
            <p className="test-data-note">{t("inbox.testLabel")}</p>
            <div className="form-actions">
              <button
                className="button secondary"
                type="button"
                onClick={() => setTestOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button
                className="button primary"
                disabled={testMutation.isPending}
              >
                <Plus />
                {t("inbox.createTest")}
              </button>
            </div>
          </form>
        </CrmDialog>
      ) : null}
    </>
  );
}
