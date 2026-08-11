"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, LockKeyhole, Mail, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { gmailNeedsAttention } from "@/lib/gmail";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function GmailConnectionsPage() {
  const t = useTranslations("gmail");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [notice, setNotice] = useState("");
  const [initialSyncMode, setInitialSyncMode] = useState<"from_now" | "recent">(
    "recent",
  );
  const [initialSyncMax, setInitialSyncMax] = useState(100);
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const readiness = useQuery({
    queryKey: ["gmail", "readiness"],
    queryFn: () => workspace.api.gmailReadiness(),
  });
  const connections = useQuery({
    queryKey: ["gmail", organizationId],
    queryFn: () => workspace.api.gmailConnections(),
  });
  const connect = useMutation({
    mutationFn: async () => {
      const redirect = `/${locale}/app/settings/channels/gmail`;
      const started = await workspace.api.startGmailOAuth(
        redirect,
        initialSyncMode,
        initialSyncMax,
      );
      if (started.mode === "live") {
        window.location.assign(started.authorization_url);
        return null;
      }
      if (!started.state) throw new Error(t("oauthStateMissing"));
      return workspace.api.completeGmailOAuth(
        started.state,
        "fake_connect:support@example.test:Althair_Support",
      );
    },
    onSuccess: async (result) => {
      if (!result) return;
      setNotice(
        t("connectedNotice", { email: result.connection.mailbox_email }),
      );
      await queryClient.invalidateQueries({
        queryKey: ["gmail", organizationId],
      });
    },
    onError: (error: Error) => setNotice(error.message),
  });
  if (readiness.isLoading || connections.isLoading) return <PageSkeleton />;
  const error = readiness.error ?? connections.error;
  if (error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={(error as Error).message}
        onRetry={() => void connections.refetch()}
      />
    );
  return (
    <>
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          editable ? (
            <button
              className="button primary"
              onClick={() => connect.mutate()}
              disabled={connect.isPending || !readiness.data?.enabled}
            >
              <Plus aria-hidden="true" />{" "}
              {connect.isPending ? t("connecting") : t("connect")}
            </button>
          ) : undefined
        }
      />
      <div className="info-banner gmail-policy-banner">
        <LockKeyhole aria-hidden="true" />
        <div>
          <strong>{t("secureTitle")}</strong>
          <p>{t("secureDescription")}</p>
        </div>
        <StatusBadge
          status={
            readiness.data?.enabled ? "ready" : "configuration_incomplete"
          }
        />
      </div>
      {editable && connections.data!.results.length === 0 ? (
        <section className="panel gmail-connect-options">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("initialSyncEyebrow")}</span>
              <h2>{t("initialSyncTitle")}</h2>
              <p>{t("initialSyncDescription")}</p>
            </div>
          </div>
          <div className="form-grid two-columns">
            <label className="field">
              <span>{t("initialSyncMode")}</span>
              <select
                value={initialSyncMode}
                onChange={(event) =>
                  setInitialSyncMode(
                    event.target.value as "from_now" | "recent",
                  )
                }
              >
                <option value="recent">{t("initialSyncRecent")}</option>
                <option value="from_now">{t("initialSyncFromNow")}</option>
              </select>
            </label>
            <label className="field">
              <span>{t("initialSyncLimit")}</span>
              <input
                type="number"
                min={1}
                max={100}
                value={initialSyncMax}
                disabled={initialSyncMode === "from_now"}
                onChange={(event) =>
                  setInitialSyncMax(Number(event.target.value))
                }
              />
            </label>
          </div>
          <p className="muted-copy">{t("labelPolicy")}</p>
        </section>
      ) : null}
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {connections.data!.results.length === 0 ? (
        <EmptyState
          icon={<Mail />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
        />
      ) : (
        <section
          className="channel-grid gmail-connection-grid"
          aria-label={t("listLabel")}
        >
          {connections.data!.results.map((connection) => (
            <article className="channel-card" key={connection.id}>
              <div className="channel-card-heading">
                <div className="channel-icon gmail-gradient">
                  <Mail aria-hidden="true" />
                </div>
                <div>
                  <h2>{connection.mailbox_email}</h2>
                  <StatusBadge status={connection.connection_status} />
                </div>
              </div>
              <p>{connection.mailbox_name || connection.mailbox_email}</p>
              <dl>
                <div>
                  <dt>{t("sync")}</dt>
                  <dd>
                    {gmailNeedsAttention(connection)
                      ? t("attention")
                      : t("healthy")}
                  </dd>
                </div>
                <div>
                  <dt>{t("watch")}</dt>
                  <dd>
                    {connection.health.watch_active
                      ? t("active")
                      : t("expired")}
                  </dd>
                </div>
                <div>
                  <dt>{t("automation")}</dt>
                  <dd>{t(`modes.${connection.automation_mode}`)}</dd>
                </div>
              </dl>
              <Link
                className="button secondary"
                href={`/app/settings/channels/gmail/${connection.id}`}
              >
                {t("manage")} <ExternalLink aria-hidden="true" />
              </Link>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
