"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ChannelConnection } from "@workspace/api-client";
import {
  AtSign,
  Mail,
  MessageCircle,
  MessagesSquare,
  Pencil,
  Phone,
  Radio,
  Smartphone,
  X,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { can } from "@/lib/permissions";
import { Link } from "@/i18n/navigation";
import { useWorkspace } from "./workspace-provider";
import {
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
  SubmitButton,
} from "./ui";

const channelTypes = [
  ["instagram", AtSign],
  ["telegram", MessagesSquare],
  ["whatsapp", MessageCircle],
  ["gmail", Mail],
  ["sms", Smartphone],
  ["voice", Phone],
  ["webchat", Radio],
] as const;

export function ChannelsPage() {
  const t = useTranslations("channels");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const id = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    workspace.membership?.organization_status !== "suspended";
  const [editing, setEditing] = useState<ChannelConnection | null>(null);
  const form = useForm<{ display_name: string; branch: string; notes: string }>(
    { defaultValues: { display_name: "", branch: "", notes: "" } },
  );
  const query = useQuery({
    queryKey: ["channels", id],
    queryFn: () => workspace.api.channels(),
  });
  const branches = useQuery({
    queryKey: ["branches", id],
    queryFn: () => workspace.api.branches(id),
  });
  useEffect(() => {
    if (editing)
      form.reset({
        display_name: editing.display_name,
        branch: editing.branch ?? "",
        notes: String(editing.configuration.notes ?? ""),
      });
  }, [editing, form]);
  const save = useMutation({
    mutationFn: (values: {
      display_name: string;
      branch: string;
      notes: string;
    }) =>
      workspace.api.updateChannel(editing!.id, {
        display_name: values.display_name,
        branch: values.branch || null,
        configuration: { ...editing!.configuration, notes: values.notes },
      }),
    onSuccess: async () => {
      setEditing(null);
      await queryClient.invalidateQueries({ queryKey: ["channels", id] });
    },
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
  return (
    <>
      <PageHeading title={t("title")} description={t("description")} />
      <div className="info-banner">
        <MessagesSquare />
        <div>
          <strong>{t("stageTitle")}</strong>
          <p>{t("stageDescription")}</p>
        </div>
      </div>
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {editing ? (
        <section className="panel inline-editor">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("editEyebrow")}</span>
              <h2>{t("editTitle", { channel: editing.display_name })}</h2>
              <p>{t("editDescription")}</p>
            </div>
            <button className="icon-button" onClick={() => setEditing(null)}>
              <X />
            </button>
          </div>
          <form onSubmit={form.handleSubmit((values) => save.mutate(values))}>
            <div className="form-grid two">
              <label className="field">
                <span>{t("displayName")}</span>
                <input required {...form.register("display_name")} />
              </label>
              <label className="field">
                <span>{t("branch")}</span>
                <select {...form.register("branch")}>
                  <option value="">{t("allBranches")}</option>
                  {branches.data?.results
                    .filter((branch) => branch.is_active)
                    .map((branch) => (
                      <option key={branch.id} value={branch.id}>
                        {branch.name}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field field-span">
                <span>{t("internalNote")}</span>
                <textarea rows={3} {...form.register("notes")} />
              </label>
            </div>
            <div className="write-only-note">{t("writeOnlyNote")}</div>
            {save.error ? (
              <div className="form-alert">{(save.error as Error).message}</div>
            ) : null}
            <div className="form-actions">
              <button
                type="button"
                className="button secondary"
                onClick={() => setEditing(null)}
              >
                {t("cancel")}
              </button>
              <SubmitButton pending={save.isPending}>{t("save")}</SubmitButton>
            </div>
          </form>
        </section>
      ) : null}
      <section className="channel-grid" aria-label={t("listLabel")}>
        {channelTypes.map(([type, Icon]) => {
          const connections = query.data!.results.filter(
            (item) => item.type === type,
          );
          const primary = connections[0];
          const state =
            primary?.status ??
            (type === "webchat" ||
            type === "instagram" ||
            type === "telegram" ||
            type === "gmail"
              ? "not_connected"
              : "planned");
          return (
            <article className="channel-card" key={type}>
              <div className="channel-card-heading">
                <div className="channel-icon">
                  <Icon />
                </div>
                <div>
                  <h2>{t(`types.${type}`)}</h2>
                  <StatusBadge status={state} />
                </div>
              </div>
              <p>
                {primary
                  ? t("configuredRecord", { provider: primary.provider })
                  : type === "webchat" ||
                      type === "instagram" ||
                      type === "telegram"
                    ? t("notConnected")
                    : t("planned")}
              </p>
              {primary ? (
                <dl>
                  <div>
                    <dt>{t("connection")}</dt>
                    <dd>{primary.display_name}</dd>
                  </div>
                  <div>
                    <dt>{t("destination")}</dt>
                    <dd>{primary.external_identifier}</dd>
                  </div>
                  <div>
                    <dt>{t("credentials")}</dt>
                    <dd>
                      {primary.has_credentials
                        ? t("storedWriteOnly")
                        : t("notStored")}
                    </dd>
                  </div>
                </dl>
              ) : null}
              {type === "telegram" ? (
                <Link
                  className="button secondary"
                  href="/app/settings/channels/telegram"
                >
                  {t("configureTelegram")}
                </Link>
              ) : type === "gmail" ? (
                <Link
                  className="button secondary"
                  href="/app/settings/channels/gmail"
                >
                  {t("configureGmail")}
                </Link>
              ) : type === "instagram" ? (
                <Link
                  className="button secondary"
                  href="/app/settings/channels/instagram"
                >
                  {t("configureInstagram")}
                </Link>
              ) : type === "webchat" ? (
                <Link
                  className="button secondary"
                  href="/app/settings/channels/web-chat"
                >
                  {t("configureWebChat")}
                </Link>
              ) : primary && editable ? (
                <button
                  className="button secondary"
                  onClick={() => setEditing(primary)}
                >
                  <Pencil />
                  {t("editMetadata")}
                </button>
              ) : (
                <button className="button secondary" disabled>
                  {t("providerSetupLater")}
                </button>
              )}
            </article>
          );
        })}
      </section>
    </>
  );
}
