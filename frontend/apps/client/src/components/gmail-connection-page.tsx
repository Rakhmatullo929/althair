"use client";

import type { GmailConnection } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  Circle,
  CircleAlert,
  MailCheck,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Unplug,
  XCircle,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

function dateTime(value: string | null, locale: string) {
  return value
    ? new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
}

export function GmailConnectionPage({
  connectionId,
}: {
  connectionId: string;
}) {
  const t = useTranslations("gmail");
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
    queryKey: ["gmail", organizationId, connectionId],
    queryFn: () => workspace.api.gmailConnection(connectionId),
  });
  const readiness = useQuery({
    queryKey: ["gmail", "readiness"],
    queryFn: () => workspace.api.gmailReadiness(),
  });
  const refresh = async () =>
    queryClient.invalidateQueries({ queryKey: ["gmail", organizationId] });
  const action = useMutation({
    mutationFn: (operation: () => Promise<GmailConnection | unknown>) =>
      operation(),
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
  const reconnect = async () => {
    const redirect = `/${locale}/app/settings/channels/gmail/${connectionId}`;
    const started = await workspace.api.reconnectGmail(connectionId, redirect);
    if (started.mode === "live") {
      window.location.assign(started.authorization_url);
      return started;
    }
    if (!started.state) throw new Error(t("oauthStateMissing"));
    return workspace.api.completeGmailOAuth(
      started.state,
      `fake_connect:${connection.mailbox_email}:Reconnected`,
    );
  };
  return (
    <>
      <Link className="back-link" href="/app/settings/channels/gmail">
        <ArrowLeft aria-hidden="true" /> {t("back")}
      </Link>
      <PageHeading
        title={connection.mailbox_email}
        description={t("detailDescription")}
        actions={<StatusBadge status={connection.connection_status} />}
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      <div className="instagram-health-grid gmail-health-grid">
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
              <dt>{t("mailbox")}</dt>
              <dd>{connection.mailbox_email}</dd>
            </div>
            <div>
              <dt>{t("token")}</dt>
              <dd>
                {connection.has_encrypted_refresh_token
                  ? t("encryptedHealthy")
                  : t("tokenMissing")}
              </dd>
            </div>
            <div>
              <dt>{t("scope")}</dt>
              <dd>
                {connection.health.scope_valid
                  ? "gmail.modify"
                  : t("scopeMissing")}
              </dd>
            </div>
            <div>
              <dt>{t("verificationMode")}</dt>
              <dd>
                {readiness.data?.live_ready
                  ? t("liveMode")
                  : t("developmentMode")}
              </dd>
            </div>
            <div>
              <dt>{t("initialSyncStatus")}</dt>
              <dd>{t(`syncStatuses.${connection.initial_sync_status}`)}</dd>
            </div>
            <div>
              <dt>{t("labels")}</dt>
              <dd>{connection.included_label_ids.join(", ")}</dd>
            </div>
            <div>
              <dt>{t("watchExpiry")}</dt>
              <dd>{dateTime(connection.watch_expiration_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastSync")}</dt>
              <dd>{dateTime(connection.health.last_sync_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastInbound")}</dt>
              <dd>{dateTime(connection.last_notification_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastOutbound")}</dt>
              <dd>{dateTime(connection.last_successful_send_at, locale)}</dd>
            </div>
          </dl>
          {editable ? (
            <div className="form-actions">
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.gmailHealth(connectionId, true),
                  )
                }
              >
                <RefreshCw aria-hidden="true" /> {t("refreshHealth")}
              </button>
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.renewGmailWatch(connectionId),
                  )
                }
              >
                <MailCheck aria-hidden="true" /> {t("renewWatch")}
              </button>
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(() => workspace.api.resyncGmail(connectionId))
                }
              >
                {t("resync")}
              </button>
              {connection.initial_sync_status === "running" ? (
                <button
                  className="button secondary"
                  onClick={() =>
                    action.mutate(() =>
                      workspace.api.cancelGmailInitialSync(connectionId),
                    )
                  }
                >
                  {t("cancelSync")}
                </button>
              ) : null}
              {connection.connection_status !== "connected" ||
              !connection.health.scope_valid ? (
                <button
                  className="button secondary"
                  onClick={() => action.mutate(reconnect)}
                >
                  <RotateCcw aria-hidden="true" /> {t("reconnect")}
                </button>
              ) : null}
            </div>
          ) : null}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("automationEyebrow")}</span>
              <h2>{t("automationTitle")}</h2>
              <p>{t("automationDescription")}</p>
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
                disabled={!editable || action.isPending}
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.updateGmailConnection(connectionId, {
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
          <div className="write-only-note">{t("aiPolicy")}</div>
        </section>
      </div>
      <section className="panel gmail-sync-settings">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("syncSettingsEyebrow")}</span>
            <h2>{t("syncSettingsTitle")}</h2>
            <p>{t("syncSettingsDescription")}</p>
          </div>
        </div>
        <div className="form-grid two-columns">
          <label className="field">
            <span>{t("initialSyncMode")}</span>
            <select
              value={connection.initial_sync_mode}
              disabled={!editable || action.isPending}
              onChange={(event) =>
                action.mutate(() =>
                  workspace.api.updateGmailConnection(connectionId, {
                    initial_sync_mode: event.target.value as
                      | "from_now"
                      | "recent",
                  }),
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
              defaultValue={connection.initial_sync_max_messages}
              disabled={!editable || action.isPending}
              onBlur={(event) =>
                action.mutate(() =>
                  workspace.api.updateGmailConnection(connectionId, {
                    initial_sync_max_messages: Number(event.target.value),
                  }),
                )
              }
            />
          </label>
          <label className="field">
            <span>{t("includedLabels")}</span>
            <input
              defaultValue={connection.included_label_ids.join(", ")}
              disabled={!editable || action.isPending}
              onBlur={(event) =>
                action.mutate(() =>
                  workspace.api.updateGmailConnection(connectionId, {
                    included_label_ids: event.target.value
                      .split(",")
                      .map((value) => value.trim())
                      .filter(Boolean),
                  }),
                )
              }
            />
          </label>
          <label className="field">
            <span>{t("excludedLabels")}</span>
            <input
              defaultValue={connection.excluded_label_ids.join(", ")}
              disabled={!editable || action.isPending}
              onBlur={(event) =>
                action.mutate(() =>
                  workspace.api.updateGmailConnection(connectionId, {
                    excluded_label_ids: event.target.value
                      .split(",")
                      .map((value) => value.trim())
                      .filter(Boolean),
                  }),
                )
              }
            />
          </label>
          <label className="field">
            <span>{t("retentionDays")}</span>
            <input
              type="number"
              min={1}
              max={3650}
              defaultValue={connection.retention_days}
              disabled={!editable || action.isPending}
              onBlur={(event) =>
                action.mutate(() =>
                  workspace.api.updateGmailConnection(connectionId, {
                    retention_days: Number(event.target.value),
                  }),
                )
              }
            />
          </label>
        </div>
        <div className="write-only-note">{t("labelPolicy")}</div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("syncHistory")}</span>
            <h2>{t("recentRuns")}</h2>
          </div>
        </div>
        {connection.recent_sync_runs.length ? (
          <div className="table-shell">
            <table>
              <thead>
                <tr>
                  <th>{t("runType")}</th>
                  <th>{t("status")}</th>
                  <th>{t("imported")}</th>
                  <th>{t("started")}</th>
                </tr>
              </thead>
              <tbody>
                {connection.recent_sync_runs.map((run) => (
                  <tr key={run.id}>
                    <td>{run.sync_type}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td>{run.imported_count}</td>
                    <td>{dateTime(run.started_at, locale)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>{t("noRuns")}</p>
        )}
      </section>
      <div className="gmail-review-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("verificationEyebrow")}</span>
              <h2>{t("verificationTitle")}</h2>
              <p>{t("verificationDescription")}</p>
            </div>
          </div>
          <ul className="gmail-verification-list">
            {connection.verification_checklist.items.map((item) => (
              <li key={item.key} className={item.ready ? "ready" : "pending"}>
                {item.ready ? (
                  <CheckCircle2 aria-hidden="true" />
                ) : (
                  <XCircle aria-hidden="true" />
                )}
                <span>{t(`verificationItems.${item.key}`)}</span>
              </li>
            ))}
          </ul>
          <div className="readonly-note">
            <CircleAlert aria-hidden="true" /> {t("approvalDisclaimer")}
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("privacyEyebrow")}</span>
              <h2>{t("privacyTitle")}</h2>
              <p>
                {t("privacyDescription", { days: connection.retention_days })}
              </p>
            </div>
          </div>
          <ul className="privacy-summary-list">
            <li>{t("privacyMailboxAccess")}</li>
            <li>{t("privacyAi")}</li>
            <li>{t("privacyExport")}</li>
            <li>{t("privacyNoAds")}</li>
            <li>{t("privacyLegalDraft")}</li>
          </ul>
          <dl className="detail-list">
            <div>
              <dt>{t("failedNotifications")}</dt>
              <dd>{connection.operations.failed_notifications}</dd>
            </div>
            <div>
              <dt>{t("queuedSends")}</dt>
              <dd>{connection.operations.queued_sends}</dd>
            </div>
            <div>
              <dt>{t("failedSends")}</dt>
              <dd>{connection.operations.failed_sends}</dd>
            </div>
          </dl>
        </section>
      </div>
      {editable && connection.connection_status !== "disconnected" ? (
        <section className="panel danger-zone">
          <div>
            <h2>{t("connectionActions")}</h2>
            <p>{t("disconnectDescription")}</p>
          </div>
          <button
            className="button secondary danger"
            onClick={() =>
              action.mutate(() => workspace.api.disconnectGmail(connectionId))
            }
          >
            <Unplug aria-hidden="true" /> {t("disconnect")}
          </button>
        </section>
      ) : null}
    </>
  );
}
