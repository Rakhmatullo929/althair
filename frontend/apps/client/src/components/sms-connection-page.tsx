"use client";

import type { SMSConnection } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Circle,
  Clipboard,
  Pause,
  Play,
  RefreshCw,
  Send,
  ShieldCheck,
  Unplug,
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

export function SMSConnectionPage({ connectionId }: { connectionId: string }) {
  const t = useTranslations("sms");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [notice, setNotice] = useState("");
  const [testFrom, setTestFrom] = useState("+15550108888");
  const [testBody, setTestBody] = useState("Hello from deterministic fake SMS");
  const [authToken, setAuthToken] = useState("");
  const [apiKeySid, setApiKeySid] = useState("");
  const [apiKeySecret, setApiKeySecret] = useState("");
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const query = useQuery({
    queryKey: ["sms", organizationId, connectionId],
    queryFn: () => workspace.api.smsConnection(connectionId),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["sms", organizationId] });
  };
  const action = useMutation({
    mutationFn: (operation: () => Promise<SMSConnection | unknown>) =>
      operation(),
    onSuccess: async () => {
      setNotice(t("saved"));
      setAuthToken("");
      setApiKeySecret("");
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const test = useMutation({
    mutationFn: () =>
      workspace.api.smsTestInbound(connectionId, {
        from: testFrom,
        body: testBody,
      }),
    onSuccess: async () => {
      setNotice(t("testAccepted"));
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
  const copy = async (value: string) => {
    await navigator.clipboard.writeText(value);
    setNotice(t("copied"));
  };
  return (
    <>
      <Link className="back-link" href="/app/settings/channels/sms">
        <ArrowLeft aria-hidden="true" /> {t("back")}
      </Link>
      <PageHeading
        title={connection.sender_address}
        description={t("detailDescription")}
        actions={<StatusBadge status={connection.status} />}
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      <div className="instagram-health-grid sms-health-grid">
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
              <dt>{t("provider")}</dt>
              <dd>{t(`providers.${connection.provider}`)}</dd>
            </div>
            <div>
              <dt>{t("sender")}</dt>
              <dd>{connection.sender_address}</dd>
            </div>
            <div>
              <dt>{t("messagingServiceSid")}</dt>
              <dd>{connection.messaging_service_sid || "—"}</dd>
            </div>
            <div>
              <dt>{t("credentials")}</dt>
              <dd>
                {connection.has_auth_token || connection.has_api_key_secret
                  ? t("encryptedHealthy")
                  : t("deploymentManaged")}
              </dd>
            </div>
            <div>
              <dt>{t("signature")}</dt>
              <dd>
                {connection.health.signature_validation_ready
                  ? t("ready")
                  : t("notReady")}
              </dd>
            </div>
            <div>
              <dt>{t("inboundHealth")}</dt>
              <dd>{connection.inbound_webhook_status}</dd>
            </div>
            <div>
              <dt>{t("statusHealth")}</dt>
              <dd>{connection.status_callback_status}</dd>
            </div>
            <div>
              <dt>{t("lastInbound")}</dt>
              <dd>{dateTime(connection.last_inbound_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastSend")}</dt>
              <dd>{dateTime(connection.last_send_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastCallback")}</dt>
              <dd>{dateTime(connection.last_status_callback_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("segmentLimits")}</dt>
              <dd>
                {t("segmentLimitsValue", {
                  human: connection.health.limits.human_max_segments,
                  ai: connection.health.limits.ai_max_segments,
                })}
              </dd>
            </div>
            <div>
              <dt>{t("rateLimits")}</dt>
              <dd>
                {t("rateLimitsValue", {
                  organization:
                    connection.health.limits.organization_sends_per_minute,
                  recipient:
                    connection.health.limits.recipient_sends_per_minute,
                })}
              </dd>
            </div>
            <div>
              <dt>{t("countryPolicy")}</dt>
              <dd>
                {connection.health.allowed_country_codes.length
                  ? connection.health.allowed_country_codes.join(", ")
                  : t("allSupportedCountries")}
              </dd>
            </div>
            <div>
              <dt>{t("deadLetters")}</dt>
              <dd>
                {t("deadLettersValue", {
                  webhooks: connection.health.failed_webhook_receipts,
                  sends: connection.health.failed_outbound_attempts,
                })}
              </dd>
            </div>
          </dl>
          {editable ? (
            <button
              className="button secondary"
              onClick={() =>
                action.mutate(() => workspace.api.smsHealth(connectionId, true))
              }
            >
              <RefreshCw aria-hidden="true" /> {t("refreshHealth")}
            </button>
          ) : null}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("webhooksEyebrow")}</span>
              <h2>{t("webhooksTitle")}</h2>
              <p>{t("webhooksDescription")}</p>
            </div>
          </div>
          <label className="field">
            <span>{t("inboundWebhook")}</span>
            <div className="copy-field">
              <input readOnly value={connection.webhook_urls.inbound} />
              <button
                className="icon-button"
                onClick={() => copy(connection.webhook_urls.inbound)}
                aria-label={t("copy")}
              >
                <Clipboard />
              </button>
            </div>
          </label>
          <label className="field">
            <span>{t("statusCallback")}</span>
            <div className="copy-field">
              <input readOnly value={connection.webhook_urls.status} />
              <button
                className="icon-button"
                onClick={() => copy(connection.webhook_urls.status)}
                aria-label={t("copy")}
              >
                <Clipboard />
              </button>
            </div>
          </label>
          <div className="write-only-note">{t("exactUrlPolicy")}</div>
        </section>
      </div>
      <div className="instagram-health-grid sms-policy-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("consentEyebrow")}</span>
              <h2>{t("consentTitle")}</h2>
              <p>{t("consentDescription")}</p>
            </div>
          </div>
          <label className="check-row">
            <input
              type="checkbox"
              checked={connection.advanced_opt_out_enabled}
              disabled={!editable || action.isPending}
              onChange={(event) =>
                action.mutate(() =>
                  workspace.api.updateSMSConnection(connectionId, {
                    advanced_opt_out_enabled: event.target.checked,
                  }),
                )
              }
            />
            <span>
              <strong>{t("advancedOptOut")}</strong>
              <small>{t("advancedOptOutHint")}</small>
            </span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={connection.allow_inbound_support}
              disabled={!editable || action.isPending}
              onChange={(event) =>
                action.mutate(() =>
                  workspace.api.updateSMSConnection(connectionId, {
                    allow_inbound_support: event.target.checked,
                  }),
                )
              }
            />
            <span>
              <strong>{t("inboundSupport")}</strong>
              <small>{t("inboundSupportHint")}</small>
            </span>
          </label>
          <div className="readonly-note">{t("consentPolicy")}</div>
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
                className={`automation-option ${connection.ai_mode === mode ? "active" : ""}`}
                disabled={!editable || action.isPending}
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.updateSMSConnection(connectionId, {
                      ai_mode: mode,
                    }),
                  )
                }
              >
                <span>
                  {connection.ai_mode === mode ? <Check /> : <Circle />}
                </span>
                <strong>{t(`modes.${mode}`)}</strong>
                <small>{t(`modeDescriptions.${mode}`)}</small>
              </button>
            ))}
          </div>
          <div className="write-only-note">{t("aiPolicy")}</div>
        </section>
      </div>
      {connection.provider === "fake" ? (
        <section className="panel sms-test-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("testEyebrow")}</span>
              <h2>{t("testTitle")}</h2>
              <p>{t("testDescription")}</p>
            </div>
            <Send aria-hidden="true" />
          </div>
          <div className="form-grid two-columns">
            <label className="field">
              <span>{t("testFrom")}</span>
              <input
                value={testFrom}
                onChange={(event) => setTestFrom(event.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("testBody")}</span>
              <textarea
                value={testBody}
                onChange={(event) => setTestBody(event.target.value)}
                rows={3}
              />
            </label>
          </div>
          <button
            className="button primary"
            disabled={!editable || test.isPending}
            onClick={() => test.mutate()}
          >
            <Send /> {t("sendTest")}
          </button>
        </section>
      ) : null}
      {editable && connection.ownership_mode === "customer_owned" ? (
        <section className="panel sms-credentials-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("rotateEyebrow")}</span>
              <h2>{t("rotateTitle")}</h2>
              <p>{t("rotateDescription")}</p>
            </div>
          </div>
          <div className="form-grid two-columns">
            <label className="field">
              <span>{t("apiKeySid")}</span>
              <input
                value={apiKeySid}
                onChange={(event) => setApiKeySid(event.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("apiKeySecret")}</span>
              <input
                type="password"
                value={apiKeySecret}
                onChange={(event) => setApiKeySecret(event.target.value)}
                autoComplete="new-password"
              />
            </label>
            <label className="field">
              <span>{t("authToken")}</span>
              <input
                type="password"
                value={authToken}
                onChange={(event) => setAuthToken(event.target.value)}
                autoComplete="new-password"
              />
            </label>
          </div>
          <button
            className="button secondary"
            disabled={!authToken && !apiKeySecret}
            onClick={() =>
              action.mutate(() =>
                workspace.api.rotateSMSCredentials(connectionId, {
                  auth_token: authToken,
                  api_key_sid: apiKeySid,
                  api_key_secret: apiKeySecret,
                }),
              )
            }
          >
            {t("rotate")}
          </button>
        </section>
      ) : null}
      {editable && connection.status !== "disconnected" ? (
        <section className="panel danger-zone">
          <div>
            <h2>{t("connectionActions")}</h2>
            <p>{t("connectionActionsDescription")}</p>
          </div>
          <div className="form-actions">
            {connection.status === "paused" ? (
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.smsAction(connectionId, "activate"),
                  )
                }
              >
                <Play /> {t("activate")}
              </button>
            ) : (
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate(() =>
                    workspace.api.smsAction(connectionId, "pause"),
                  )
                }
              >
                <Pause /> {t("pause")}
              </button>
            )}
            <button
              className="button secondary danger"
              onClick={() =>
                action.mutate(() =>
                  workspace.api.smsAction(connectionId, "disconnect"),
                )
              }
            >
              <Unplug /> {t("disconnect")}
            </button>
          </div>
        </section>
      ) : null}
    </>
  );
}
