"use client";

import type { FollowUpTask } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Check, CircleX, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { canManageCrm, crmQueryKeys, taskBucket } from "@/lib/crm";
import { CrmDialog, formatDateTime } from "./crm-shared";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

type Bucket = "overdue" | "today" | "upcoming" | "completed";

export function TasksPage() {
  const t = useTranslations("crm");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const membership = workspace.membership!;
  const editable =
    canManageCrm(membership.role) &&
    !["suspended", "archived"].includes(membership.organization_status);
  const [createOpen, setCreateOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [contactId, setContactId] = useState("");
  const [notice, setNotice] = useState("");
  const tasks = useQuery({
    queryKey: crmQueryKeys.tasks(organizationId),
    queryFn: () => workspace.api.followUpTasks({ page_size: 100 }),
  });
  const contacts = useQuery({
    queryKey: crmQueryKeys.contacts(organizationId),
    queryFn: () => workspace.api.contacts({ status: "active", page_size: 100 }),
  });
  const refresh = () =>
    queryClient.invalidateQueries({
      queryKey: crmQueryKeys.root(organizationId),
    });
  const mutation = useMutation({
    mutationFn: ({
      task,
      status,
    }: {
      task: FollowUpTask;
      status: FollowUpTask["status"];
    }) => workspace.api.updateFollowUpTask(task.id, { status }),
    onSuccess: async () => {
      setNotice(t("common.saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createFollowUpTask({
        title,
        due_at: new Date(dueAt).toISOString(),
        related_contact: contactId,
        assigned_membership: membership.id,
      }),
    onSuccess: async () => {
      setCreateOpen(false);
      setTitle("");
      setDueAt("");
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const grouped: Record<Bucket, FollowUpTask[]> = {
    overdue: [],
    today: [],
    upcoming: [],
    completed: [],
  };
  for (const task of tasks.data?.results ?? [])
    grouped[taskBucket(task.due_at, task.status)].push(task);

  return (
    <>
      <PageHeading
        title={t("tasks.title")}
        description={t("tasks.description")}
        actions={
          editable ? (
            <button
              className="button primary"
              onClick={() => {
                setContactId(contacts.data?.results[0]?.id ?? "");
                const tomorrow = new Date(Date.now() + 86_400_000);
                setDueAt(
                  new Date(
                    tomorrow.getTime() - tomorrow.getTimezoneOffset() * 60_000,
                  )
                    .toISOString()
                    .slice(0, 16),
                );
                setCreateOpen(true);
              }}
            >
              <Plus />
              {t("tasks.new")}
            </button>
          ) : undefined
        }
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {tasks.isLoading ? (
        <PageSkeleton />
      ) : tasks.error ? (
        <ErrorState
          title={t("common.error")}
          description={(tasks.error as Error).message}
        />
      ) : (tasks.data?.results.length ?? 0) > 0 ? (
        <div className="task-board">
          {(["overdue", "today", "upcoming", "completed"] as Bucket[]).map(
            (bucket) => (
              <section className={`task-group task-${bucket}`} key={bucket}>
                <header>
                  <div>
                    <span className="eyebrow">
                      {t(`tasks.bucket.${bucket}`)}
                    </span>
                    <h2>{t(`tasks.bucketTitle.${bucket}`)}</h2>
                  </div>
                  <span className="task-count">{grouped[bucket].length}</span>
                </header>
                {grouped[bucket].length ? (
                  <ul>
                    {grouped[bucket].map((task) => (
                      <li key={task.id}>
                        <span className="task-check">
                          <CalendarClock />
                        </span>
                        <div className="task-copy">
                          <strong>{task.title}</strong>
                          <p>
                            {task.contact_name ||
                              task.lead_title ||
                              t("tasks.relatedConversation")}
                          </p>
                          <time dateTime={task.due_at}>
                            {formatDateTime(task.due_at, locale)}
                          </time>
                          <span>
                            {task.assigned_name ?? t("status.unassigned")}
                          </span>
                        </div>
                        <StatusBadge status={task.status} />
                        {editable && task.status === "open" ? (
                          <div className="task-actions">
                            <button
                              className="icon-button"
                              aria-label={t("tasks.completeTask", {
                                title: task.title,
                              })}
                              onClick={() =>
                                mutation.mutate({ task, status: "completed" })
                              }
                            >
                              <Check />
                            </button>
                            <button
                              className="icon-button danger"
                              aria-label={t("tasks.cancelTask", {
                                title: task.title,
                              })}
                              onClick={() =>
                                mutation.mutate({ task, status: "cancelled" })
                              }
                            >
                              <CircleX />
                            </button>
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="task-empty">{t("tasks.noneInBucket")}</p>
                )}
              </section>
            ),
          )}
        </div>
      ) : (
        <EmptyState
          icon={<CalendarClock />}
          title={t("tasks.emptyTitle")}
          description={t("tasks.emptyDescription")}
        />
      )}
      {createOpen ? (
        <CrmDialog
          title={t("tasks.createTitle")}
          description={t("tasks.createDescription")}
          closeLabel={t("common.close")}
          onClose={() => setCreateOpen(false)}
        >
          <form
            className="form-grid"
            onSubmit={(event) => {
              event.preventDefault();
              create.mutate();
            }}
          >
            <label className="field">
              {t("tasks.taskTitle")}
              <input
                autoFocus
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
                maxLength={200}
              />
            </label>
            <label className="field">
              {t("tasks.dueAt")}
              <input
                type="datetime-local"
                value={dueAt}
                onChange={(event) => setDueAt(event.target.value)}
                required
              />
            </label>
            <label className="field">
              {t("tasks.contact")}
              <select
                value={contactId}
                onChange={(event) => setContactId(event.target.value)}
                required
              >
                {contacts.data?.results.map((contact) => (
                  <option key={contact.id} value={contact.id}>
                    {contact.display_name}
                  </option>
                ))}
              </select>
            </label>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setCreateOpen(false)}
              >
                {t("common.cancel")}
              </button>
              <button
                className="button primary"
                disabled={create.isPending || !contactId}
              >
                <Plus />
                {t("tasks.create")}
              </button>
            </div>
          </form>
        </CrmDialog>
      ) : null}
    </>
  );
}
