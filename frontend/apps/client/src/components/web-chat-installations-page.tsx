"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe2, Plus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

export function WebChatInstallationsPage() {
  const t = useTranslations("webChat");
  const workspace = useWorkspace();
  const cache = useQueryClient();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const query = useQuery({
    queryKey: ["web-chat-installations", workspace.selectedOrganizationId],
    queryFn: () => workspace.api.webChatInstallations(),
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createWebChatInstallation({
        display_name: name,
        allowed_origins: ["http://localhost:3001"],
      }),
    onSuccess: async () => {
      setCreating(false);
      setName("");
      await cache.invalidateQueries({ queryKey: ["web-chat-installations"] });
    },
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("error")}
        description={(query.error as Error).message}
        onRetry={() => void query.refetch()}
      />
    );
  return (
    <>
      <PageHeading title={t("title")} description={t("description")} />
      <div className="info-banner">
        <Globe2 />
        <div>
          <strong>{t("publicStage")}</strong>
          <p>{t("publicStageHint")}</p>
        </div>
      </div>
      {editable ? (
        <div className="page-action-row">
          <button className="button primary" onClick={() => setCreating(true)}>
            <Plus />
            {t("newInstallation")}
          </button>
          <Link className="button secondary" href="/demo">
            {t("openDemo")}
          </Link>
        </div>
      ) : null}
      {creating ? (
        <section className="panel webchat-create-panel">
          <h2>{t("createTitle")}</h2>
          <label className="field">
            <span>{t("displayName")}</span>
            <input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          {create.error ? (
            <div className="form-alert">{(create.error as Error).message}</div>
          ) : null}
          <div className="form-actions">
            <button
              className="button secondary"
              onClick={() => setCreating(false)}
            >
              {t("cancel")}
            </button>
            <button
              className="button primary"
              disabled={!name.trim() || create.isPending}
              onClick={() => create.mutate()}
            >
              {t("create")}
            </button>
          </div>
        </section>
      ) : null}
      <section
        className="webchat-installation-grid"
        aria-label={t("listLabel")}
      >
        {query.data?.results.map((item) => (
          <article className="panel webchat-installation-card" key={item.id}>
            <div className="panel-heading">
              <div>
                <span className="eyebrow">{t("installation")}</span>
                <h2>{item.display_name}</h2>
              </div>
              <StatusBadge status={item.status} />
            </div>
            <dl className="webchat-facts">
              <div>
                <dt>{t("origins")}</dt>
                <dd>{item.health.origin_count}</dd>
              </div>
              <div>
                <dt>{t("activeSessions")}</dt>
                <dd>{item.session_counts.active}</dd>
              </div>
              <div>
                <dt>{t("aiMode")}</dt>
                <dd>{item.ai_mode}</dd>
              </div>
            </dl>
            <Link
              className="button secondary"
              href={`/app/settings/channels/web-chat/${item.id}`}
            >
              {t("configure")}
            </Link>
          </article>
        ))}
        {!query.data?.results.length ? (
          <div className="empty-state">
            <Globe2 />
            <h2>{t("emptyTitle")}</h2>
            <p>{t("emptyHint")}</p>
          </div>
        ) : null}
      </section>
    </>
  );
}
