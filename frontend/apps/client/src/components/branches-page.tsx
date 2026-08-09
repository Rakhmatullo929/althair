"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Branch, WorkingHours } from "@workspace/api-client";
import {
  Archive,
  Clock3,
  GitBranch,
  MapPin,
  Pencil,
  Phone,
  Plus,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
  SubmitButton,
} from "./ui";

type BranchValues = {
  name: string;
  address: string;
  phone: string;
  email: string;
  timezone: string;
  open: string;
  close: string;
  is_active: boolean;
};
const defaultValues: BranchValues = {
  name: "",
  address: "",
  phone: "",
  email: "",
  timezone: "Asia/Tashkent",
  open: "09:00",
  close: "18:00",
  is_active: true,
};

export function BranchesPage() {
  const t = useTranslations("branches");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_company") &&
    workspace.membership?.organization_status !== "suspended";
  const [editing, setEditing] = useState<Branch | "new" | null>(null);
  const form = useForm<BranchValues>({ defaultValues });
  const query = useQuery({
    queryKey: ["branches", organizationId],
    queryFn: () => workspace.api.branches(organizationId),
  });
  useEffect(() => {
    if (!editing || editing === "new") form.reset(defaultValues);
    else {
      const hours = editing.working_hours.mon?.[0];
      form.reset({
        name: editing.name,
        address: editing.address,
        phone: editing.phone,
        email: editing.email,
        timezone: editing.timezone,
        open: hours?.open ?? "09:00",
        close: hours?.close ?? "18:00",
        is_active: editing.is_active,
      });
    }
  }, [editing, form]);
  const save = useMutation({
    mutationFn: async (values: BranchValues) => {
      const weekdayPeriods = [{ open: values.open, close: values.close }];
      const working_hours: WorkingHours = {
        mon: weekdayPeriods,
        tue: weekdayPeriods,
        wed: weekdayPeriods,
        thu: weekdayPeriods,
        fri: weekdayPeriods,
      };
      const body = {
        name: values.name,
        address: values.address,
        phone: values.phone,
        email: values.email,
        timezone: values.timezone,
        working_hours,
        is_active: values.is_active,
      };
      return editing === "new"
        ? workspace.api.createBranch(organizationId, body)
        : workspace.api.updateBranch(
            organizationId,
            (editing as Branch).id,
            body,
          );
    },
    onSuccess: async () => {
      setEditing(null);
      await queryClient.invalidateQueries({
        queryKey: ["branches", organizationId],
      });
    },
  });
  const archive = useMutation({
    mutationFn: (branchId: string) =>
      workspace.api.archiveBranch(organizationId, branchId),
    onSuccess: async () =>
      queryClient.invalidateQueries({ queryKey: ["branches", organizationId] }),
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={(query.error as Error).message}
        onRetry={() => void query.refetch()}
      />
    );
  const rows = query.data!.results;
  return (
    <>
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          editable ? (
            <button
              className="button primary"
              onClick={() => setEditing("new")}
            >
              <Plus />
              {t("add")}
            </button>
          ) : undefined
        }
      />
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {editing ? (
        <section
          className="panel inline-editor"
          aria-labelledby="branch-editor-title"
        >
          <div className="panel-heading">
            <div>
              <span className="eyebrow">
                {editing === "new" ? t("newEyebrow") : t("editEyebrow")}
              </span>
              <h2 id="branch-editor-title">
                {editing === "new"
                  ? t("newTitle")
                  : t("editTitle", { name: editing.name })}
              </h2>
            </div>
            <button
              className="icon-button"
              onClick={() => setEditing(null)}
              aria-label={t("close")}
            >
              <X />
            </button>
          </div>
          <form onSubmit={form.handleSubmit((values) => save.mutate(values))}>
            <div className="form-grid two">
              <label className="field">
                <span>{t("name")}</span>
                <input required {...form.register("name")} />
              </label>
              <label className="field">
                <span>{t("timezone")}</span>
                <input required {...form.register("timezone")} />
              </label>
              <label className="field field-span">
                <span>{t("address")}</span>
                <input {...form.register("address")} />
              </label>
              <label className="field">
                <span>{t("phone")}</span>
                <input type="tel" {...form.register("phone")} />
              </label>
              <label className="field">
                <span>{t("email")}</span>
                <input type="email" {...form.register("email")} />
              </label>
              <label className="field">
                <span>{t("opens")}</span>
                <input type="time" {...form.register("open")} />
              </label>
              <label className="field">
                <span>{t("closes")}</span>
                <input type="time" {...form.register("close")} />
              </label>
            </div>
            <label className="checkbox-field">
              <input type="checkbox" {...form.register("is_active")} />
              <span>{t("active")}</span>
            </label>
            {save.error ? (
              <div className="form-alert">{(save.error as Error).message}</div>
            ) : null}
            <div className="form-actions">
              <button
                className="button secondary"
                type="button"
                onClick={() => setEditing(null)}
              >
                {t("cancel")}
              </button>
              <SubmitButton pending={save.isPending}>{t("save")}</SubmitButton>
            </div>
          </form>
        </section>
      ) : null}
      {rows.length ? (
        <section className="card-list" aria-label={t("listLabel")}>
          {rows.map((branch) => (
            <article
              className={`branch-card ${!branch.is_active ? "archived" : ""}`}
              key={branch.id}
            >
              <div className="card-top">
                <div className="branch-mark">
                  <GitBranch />
                </div>
                <div>
                  <h2>{branch.name}</h2>
                  <StatusBadge
                    status={branch.is_active ? "active" : "archived"}
                  />
                </div>
              </div>
              <dl className="detail-list">
                <div>
                  <MapPin />
                  <dt>{t("address")}</dt>
                  <dd>{branch.address || t("notProvided")}</dd>
                </div>
                <div>
                  <Phone />
                  <dt>{t("phone")}</dt>
                  <dd>{branch.phone || t("notProvided")}</dd>
                </div>
                <div>
                  <Clock3 />
                  <dt>{t("hours")}</dt>
                  <dd>
                    {branch.working_hours.mon?.[0]
                      ? `${branch.working_hours.mon[0].open}–${branch.working_hours.mon[0].close}`
                      : t("notProvided")}
                  </dd>
                </div>
              </dl>
              {editable ? (
                <div className="card-actions">
                  <button
                    className="button secondary"
                    onClick={() => setEditing(branch)}
                  >
                    <Pencil />
                    {t("edit")}
                  </button>
                  {branch.is_active ? (
                    <button
                      className="button danger-ghost"
                      onClick={() => {
                        if (
                          window.confirm(
                            t("archiveConfirm", { name: branch.name }),
                          )
                        )
                          archive.mutate(branch.id);
                      }}
                    >
                      <Archive />
                      {t("archive")}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </article>
          ))}
        </section>
      ) : (
        <EmptyState
          icon={<GitBranch />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
          action={
            editable ? (
              <button
                className="button primary"
                onClick={() => setEditing("new")}
              >
                <Plus />
                {t("addFirst")}
              </button>
            ) : undefined
          }
        />
      )}
    </>
  );
}
