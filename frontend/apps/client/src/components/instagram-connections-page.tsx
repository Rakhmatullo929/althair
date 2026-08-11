"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, ExternalLink, LockKeyhole, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { instagramNeedsAttention } from "@/lib/instagram";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function InstagramConnectionsPage() {
  const t = useTranslations("instagram");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [notice, setNotice] = useState("");
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const query = useQuery({
    queryKey: ["instagram", organizationId],
    queryFn: () => workspace.api.instagramConnections(),
  });
  const connect = useMutation({
    mutationFn: async () => {
      const redirect = `/${locale}/app/settings/channels/instagram`;
      const started = await workspace.api.startInstagramOAuth(redirect);
      if (started.mode === "live") {
        window.location.assign(started.authorization_url);
        return null;
      }
      if (!started.state) throw new Error(t("oauthStateMissing"));
      return workspace.api.completeInstagramOAuth(
        started.state,
        "fake_connect:ig_professional_demo:althair_demo:BUSINESS",
      );
    },
    onSuccess: async (result) => {
      if (!result) return;
      setNotice(t("connectedNotice", { username: result.connection.username }));
      await queryClient.invalidateQueries({
        queryKey: ["instagram", organizationId],
      });
    },
    onError: (error: Error) => setNotice(error.message),
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
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          editable ? (
            <button
              className="button primary"
              type="button"
              onClick={() => connect.mutate()}
              disabled={connect.isPending}
            >
              <Plus aria-hidden="true" />{" "}
              {connect.isPending ? t("connecting") : t("connect")}
            </button>
          ) : undefined
        }
      />
      <div className="info-banner instagram-policy-banner">
        <LockKeyhole aria-hidden="true" />
        <div>
          <strong>{t("secureTitle")}</strong>
          <p>{t("secureDescription")}</p>
        </div>
      </div>
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {query.data!.results.length === 0 ? (
        <EmptyState
          icon={<AtSign />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
        />
      ) : (
        <section
          className="channel-grid instagram-connection-grid"
          aria-label={t("listLabel")}
        >
          {query.data!.results.map((connection) => (
            <article className="channel-card" key={connection.id}>
              <div className="channel-card-heading">
                <div className="channel-icon instagram-gradient">
                  <AtSign aria-hidden="true" />
                </div>
                <div>
                  <h2>@{connection.username}</h2>
                  <StatusBadge status={connection.connection_status} />
                </div>
              </div>
              <p>{connection.profile_name || connection.account_type}</p>
              <dl>
                <div>
                  <dt>{t("permissions")}</dt>
                  <dd>
                    {instagramNeedsAttention(connection)
                      ? t("attention")
                      : t("healthy")}
                  </dd>
                </div>
                <div>
                  <dt>{t("webhook")}</dt>
                  <dd>{connection.webhook_subscription_status}</dd>
                </div>
                <div>
                  <dt>{t("automation")}</dt>
                  <dd>{t(`modes.${connection.automation_mode}`)}</dd>
                </div>
              </dl>
              <Link
                className="button secondary"
                href={`/app/settings/channels/instagram/${connection.id}`}
              >
                {t("openHealth")} <ExternalLink aria-hidden="true" />
              </Link>
            </article>
          ))}
        </section>
      )}
    </>
  );
}
