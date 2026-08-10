"use client";

import type { InstagramConnection } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  Circle,
  RefreshCw,
  ShieldCheck,
  Unplug,
  Webhook,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

function value(value: string | null, locale: string) {
  return value
    ? new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
}

export function InstagramConnectionPage({
  connectionId,
}: {
  connectionId: string;
}) {
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
    queryKey: ["instagram", organizationId, connectionId],
    queryFn: () => workspace.api.instagramConnection(connectionId),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["instagram", organizationId],
    });
  };
  const mutate = useMutation({
    mutationFn: (action: () => Promise<InstagramConnection | unknown>) =>
      action(),
    onSuccess: async () => {
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={(query.error as Error)?.message ?? t("notFound")}
        onRetry={() => void query.refetch()}
      />
    );
  const connection = query.data;
  const health = connection.health;
  return (
    <>
      <Link className="back-link" href="/app/settings/channels/instagram">
        <ArrowLeft aria-hidden="true" /> {t("back")}
      </Link>
      <PageHeading
        title={`@${connection.username}`}
        description={connection.profile_name || connection.account_type}
        actions={<StatusBadge status={connection.connection_status} />}
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      <div className="instagram-health-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("healthEyebrow")}</span>
              <h2>{t("healthTitle")}</h2>
            </div>
            <ShieldCheck aria-hidden="true" />
          </div>
          <dl className="detail-list">
            <div>
              <dt>{t("account")}</dt>
              <dd>
                @{connection.username} · {connection.account_type}
              </dd>
            </div>
            <div>
              <dt>{t("token")}</dt>
              <dd>
                {health.token_present && !health.token_expired
                  ? t("encryptedHealthy")
                  : t("tokenAttention")}
              </dd>
            </div>
            <div>
              <dt>{t("tokenExpiry")}</dt>
              <dd>{value(connection.token_expires_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("permissions")}</dt>
              <dd>
                {health.permissions_ok
                  ? t("healthy")
                  : health.missing_permissions.join(", ")}
              </dd>
            </div>
            <div>
              <dt>{t("webhook")}</dt>
              <dd>{health.webhook_subscription}</dd>
            </div>
            <div>
              <dt>{t("graphVersion")}</dt>
              <dd>{health.graph_api_version}</dd>
            </div>
            <div>
              <dt>{t("appMode")}</dt>
              <dd>{health.app_mode}</dd>
            </div>
            <div>
              <dt>{t("lastWebhook")}</dt>
              <dd>{value(health.last_webhook_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastSend")}</dt>
              <dd>{value(health.last_send_at, locale)}</dd>
            </div>
          </dl>
          {editable ? (
            <button
              className="button secondary"
              onClick={() =>
                mutate.mutate(() =>
                  workspace.api.instagramHealth(connectionId, true),
                )
              }
            >
              <RefreshCw aria-hidden="true" /> {t("refreshHealth")}
            </button>
          ) : null}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("automationEyebrow")}</span>
              <h2>{t("automationTitle")}</h2>
              <p>{t("windowExplanation")}</p>
            </div>
          </div>
          <div
            className="automation-options"
            role="radiogroup"
            aria-label={t("automationTitle")}
          >
            {(["manual", "suggest", "autopilot"] as const).map((mode) => (
              <button
                key={mode}
                className={`automation-option ${connection.automation_mode === mode ? "active" : ""}`}
                disabled={!editable || mutate.isPending}
                onClick={() =>
                  mutate.mutate(() =>
                    workspace.api.updateInstagramConnection(connectionId, {
                      automation_mode: mode,
                    }),
                  )
                }
              >
                <span>
                  {connection.automation_mode === mode ? (
                    <Check aria-hidden="true" />
                  ) : (
                    <Circle aria-hidden="true" />
                  )}
                </span>
                <strong>{t(`modes.${mode}`)}</strong>
                <small>{t(`modeDescriptions.${mode}`)}</small>
              </button>
            ))}
          </div>
          <div className="info-banner compact">
            <AlertTriangle aria-hidden="true" />
            <div>
              <strong>{t("humanAgentTitle")}</strong>
              <p>{t("humanAgentDescription")}</p>
            </div>
          </div>
        </section>
      </div>
      <section className="panel app-review-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("reviewEyebrow")}</span>
            <h2>{t("reviewTitle")}</h2>
            <p>{t("reviewDescription")}</p>
          </div>
          <Webhook aria-hidden="true" />
        </div>
        <ul className="checklist">
          {connection.app_review_checklist.map((item) => (
            <li key={item.key} className={item.ready ? "ready" : "pending"}>
              {item.ready ? (
                <Check aria-hidden="true" />
              ) : (
                <Circle aria-hidden="true" />
              )}
              <span>{t(`reviewItems.${item.key}`)}</span>
            </li>
          ))}
        </ul>
      </section>
      {editable ? (
        <section className="panel danger-zone">
          <div>
            <h2>{t("connectionActions")}</h2>
            <p>{t("connectionActionsDescription")}</p>
          </div>
          <div className="form-actions">
            {connection.connection_status === "disconnected" ? (
              <button
                className="button secondary"
                onClick={() =>
                  mutate.mutate(() =>
                    workspace.api.reconnectInstagram(connectionId),
                  )
                }
              >
                <RefreshCw aria-hidden="true" /> {t("reconnect")}
              </button>
            ) : (
              <button
                className="button secondary danger"
                onClick={() =>
                  mutate.mutate(() =>
                    workspace.api.disconnectInstagram(connectionId),
                  )
                }
              >
                <Unplug aria-hidden="true" /> {t("disconnect")}
              </button>
            )}
          </div>
        </section>
      ) : (
        <div className="readonly-note">{t("readOnly")}</div>
      )}
    </>
  );
}
