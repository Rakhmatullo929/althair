"use client";

import type { Conversation, OrganizationRole } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  BotOff,
  Check,
  CircleAlert,
  Hand,
  PencilLine,
  Play,
  RotateCcw,
  ShieldAlert,
  Sparkles,
  ThumbsDown,
  Wrench,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { canManageCrm, canOperateCrm, crmQueryKeys } from "@/lib/crm";
import { aiQueryKeys } from "@/lib/ai-runtime";
import { StatusBadge } from "./ui";
import { useWorkspace } from "./workspace-provider";

export function ConversationAIPanel({
  conversation,
  organizationId,
  role,
  readOnly,
  onRefresh,
}: {
  conversation: Conversation;
  organizationId: string;
  role: OrganizationRole;
  readOnly: boolean;
  onRefresh: () => Promise<void>;
}) {
  const t = useTranslations("aiInbox");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState("");
  const [editedBody, setEditedBody] = useState("");
  const [editing, setEditing] = useState(false);
  const runKey = useMemo(
    () => aiQueryKeys.conversation(organizationId, conversation.id),
    [conversation.id, organizationId],
  );
  const runs = useQuery({
    queryKey: runKey,
    queryFn: () => workspace.api.conversationAIRuns(conversation.id),
    refetchInterval: 2500,
  });
  const latest = runs.data?.results[0];
  const draft = latest?.draft?.status === "pending" ? latest.draft : null;
  const handoff = latest?.handoffs.find((item) => item.status !== "resolved");
  const pending = ["queued", "running"].includes(latest?.status ?? "");

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: runKey }),
      queryClient.invalidateQueries({
        queryKey: crmQueryKeys.root(organizationId),
      }),
      onRefresh(),
    ]);
  };
  const action = useMutation({
    mutationFn: async (callback: () => Promise<unknown>) => callback(),
    onSuccess: async () => {
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const canResume = canManageCrm(role) && !readOnly;
  const canOperate = canOperateCrm(role) && !readOnly;
  const paused = ["off", "paused_by_human", "handoff_required"].includes(
    conversation.ai_state,
  );

  return (
    <section className="conversation-ai-panel" aria-label={t("panelLabel")}>
      <header>
        <div>
          <span className="ai-orbit">
            <Sparkles aria-hidden="true" />
          </span>
          <div>
            <strong>{t("title")}</strong>
            <small>{t("publishedOnly")}</small>
          </div>
        </div>
        <StatusBadge status={conversation.ai_state} />
      </header>
      <div className="ai-panel-actions">
        {!paused && canOperate ? (
          <button
            className="button secondary"
            onClick={() =>
              action.mutate(() =>
                workspace.api.pauseConversationAI(conversation.id),
              )
            }
            disabled={action.isPending}
          >
            <BotOff aria-hidden="true" /> {t("pause")}
          </button>
        ) : null}
        {paused && canResume && !handoff ? (
          <button
            className="button secondary"
            onClick={() =>
              action.mutate(() =>
                workspace.api.resumeConversationAI(conversation.id, "suggest"),
              )
            }
            disabled={action.isPending}
          >
            <Play aria-hidden="true" /> {t("resumeSuggest")}
          </button>
        ) : null}
        {conversation.ai_state === "suggest" && canOperate ? (
          <button
            className="button primary"
            onClick={() =>
              action.mutate(() =>
                workspace.api.generateAIDraft(
                  conversation.id,
                  crypto.randomUUID(),
                ),
              )
            }
            disabled={action.isPending || pending}
          >
            <Bot aria-hidden="true" /> {t("generate")}
          </button>
        ) : null}
      </div>
      {notice ? (
        <p className="ai-inline-notice" role="status">
          {notice}
        </p>
      ) : null}
      {conversation.ai_state === "paused_by_human" ? (
        <div className="ai-human-pause" role="status">
          <Hand aria-hidden="true" />
          <span>
            <strong>{t("humanPaused")}</strong>
            {t("humanPausedHint")}
          </span>
        </div>
      ) : null}
      {pending ? (
        <div className="ai-pending" role="status">
          <span className="pulse-dot" />
          {t("working")}
        </div>
      ) : null}

      {handoff ? (
        <article className="ai-handoff-banner">
          <ShieldAlert aria-hidden="true" />
          <div>
            <strong>{t("handoffTitle")}</strong>
            <p>{handoff.safe_summary}</p>
            <small>{handoff.reason_code}</small>
          </div>
          <div>
            {handoff.status === "open" && canOperate ? (
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.acknowledgeAIHandoff(handoff.id),
                  )
                }
              >
                {t("acknowledge")}
              </button>
            ) : null}
            {canResume ? (
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(async () => {
                    await workspace.api.resolveAIHandoff(handoff.id);
                    return workspace.api.resumeConversationAI(
                      conversation.id,
                      "suggest",
                    );
                  })
                }
              >
                {t("resolveResume")}
              </button>
            ) : null}
          </div>
        </article>
      ) : null}

      {latest?.tool_calls.length ? (
        <div className="ai-tool-proposals">
          <h3>
            <Wrench aria-hidden="true" />
            {t("tools")}
          </h3>
          {latest.tool_calls.map((tool) => (
            <article key={tool.id}>
              <div>
                <strong>{tool.tool_name}</strong>
                <StatusBadge status={tool.status} />
                <p>
                  {tool.status === "succeeded"
                    ? JSON.stringify(tool.output_redacted)
                    : t("serverValidates")}
                </p>
              </div>
              {tool.status === "awaiting_approval" &&
              canManageCrm(role) &&
              !readOnly ? (
                <div>
                  <button
                    className="button primary"
                    onClick={() =>
                      action.mutate(() =>
                        workspace.api.approveAIToolCall(tool.id),
                      )
                    }
                  >
                    <Check aria-hidden="true" />
                    {t("approveTool")}
                  </button>
                  <button
                    className="button danger-ghost"
                    onClick={() =>
                      action.mutate(() =>
                        workspace.api.rejectAIToolCall(tool.id),
                      )
                    }
                  >
                    <X aria-hidden="true" />
                    {t("rejectTool")}
                  </button>
                </div>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}

      {draft ? (
        <article className="ai-draft-card">
          <header>
            <span>
              <PencilLine aria-hidden="true" />
              {t("draftTitle")}
            </span>
            <small>
              {t("generatedLabel")} · {draft.language.toUpperCase()}
            </small>
          </header>
          {editing ? (
            <textarea
              aria-label={t("editLabel")}
              rows={4}
              value={editedBody}
              onChange={(event) => setEditedBody(event.target.value)}
            />
          ) : (
            <p>{draft.body}</p>
          )}
          {canOperate ? (
            <div className="ai-draft-actions">
              <button
                className="button primary"
                onClick={() =>
                  action.mutate(() => workspace.api.approveAIDraft(draft.id))
                }
                disabled={action.isPending}
              >
                <Check aria-hidden="true" />
                {t("approveSend")}
              </button>
              {editing ? (
                <button
                  className="button secondary"
                  onClick={() =>
                    action.mutate(() =>
                      workspace.api.editAndSendAIDraft(draft.id, editedBody),
                    )
                  }
                  disabled={!editedBody.trim() || action.isPending}
                >
                  <PencilLine aria-hidden="true" />
                  {t("sendEdited")}
                </button>
              ) : (
                <button
                  className="button secondary"
                  onClick={() => {
                    setEditedBody(draft.body);
                    setEditing(true);
                  }}
                >
                  <PencilLine aria-hidden="true" />
                  {t("edit")}
                </button>
              )}
              <button
                className="button danger-ghost"
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.rejectAIDraft(
                      draft.id,
                      "Rejected from Inbox",
                    ),
                  )
                }
                disabled={action.isPending}
              >
                <ThumbsDown aria-hidden="true" />
                {t("reject")}
              </button>
            </div>
          ) : null}
        </article>
      ) : null}

      {latest?.status === "failed" ? (
        <div className="ai-run-failure" role="alert">
          <CircleAlert aria-hidden="true" />
          <div>
            <strong>{t("failedTitle")}</strong>
            <p>
              {latest.error_category}: {latest.error_code}
            </p>
          </div>
          {canOperate ? (
            <button
              className="button secondary"
              onClick={() =>
                action.mutate(() =>
                  workspace.api.generateAIDraft(
                    conversation.id,
                    crypto.randomUUID(),
                  ),
                )
              }
            >
              <RotateCcw aria-hidden="true" />
              {t("retry")}
            </button>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
