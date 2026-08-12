"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, LockKeyhole, Plus, Smartphone } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { smsNeedsAttention } from "@/lib/sms";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

type Provider = "fake" | "twilio";
type Ownership = "platform_managed" | "customer_owned";

export function SMSConnectionsPage() {
  const t = useTranslations("sms");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [provider, setProvider] = useState<Provider>("fake");
  const [ownership, setOwnership] = useState<Ownership>("platform_managed");
  const [sender, setSender] = useState("+15550109999");
  const [displayName, setDisplayName] = useState("Customer SMS");
  const [accountSid, setAccountSid] = useState("");
  const [messagingServiceSid, setMessagingServiceSid] = useState("");
  const [apiKeySid, setApiKeySid] = useState("");
  const [apiKeySecret, setApiKeySecret] = useState("");
  const [authToken, setAuthToken] = useState("");
  const [notice, setNotice] = useState("");
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const readiness = useQuery({
    queryKey: ["sms", "readiness"],
    queryFn: () => workspace.api.smsReadiness(),
  });
  const connections = useQuery({
    queryKey: ["sms", organizationId],
    queryFn: () => workspace.api.smsConnections(),
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createSMSConnection({
        display_name: displayName,
        provider,
        ownership_mode: ownership,
        sender_address: sender,
        account_sid: accountSid,
        messaging_service_sid: messagingServiceSid,
        api_key_sid: apiKeySid,
        api_key_secret: apiKeySecret,
        auth_token: authToken,
        advanced_opt_out_enabled: provider === "twilio",
      }),
    onSuccess: async () => {
      setApiKeySecret("");
      setAuthToken("");
      setNotice(t("created"));
      await queryClient.invalidateQueries({
        queryKey: ["sms", organizationId],
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
  const rows = connections.data?.results ?? [];
  return (
    <>
      <PageHeading title={t("title")} description={t("description")} />
      <div className="info-banner sms-policy-banner">
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
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {editable && rows.length === 0 ? (
        <section className="panel sms-connect-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("setupEyebrow")}</span>
              <h2>{t("setupTitle")}</h2>
              <p>{t("setupDescription")}</p>
            </div>
            <Plus aria-hidden="true" />
          </div>
          <div className="form-grid two-columns">
            <label className="field">
              <span>{t("provider")}</span>
              <select
                value={provider}
                onChange={(event) =>
                  setProvider(event.target.value as Provider)
                }
              >
                <option value="fake">{t("providers.fake")}</option>
                <option value="twilio" disabled={!readiness.data?.live_ready}>
                  {t("providers.twilio")}
                </option>
              </select>
            </label>
            <label className="field">
              <span>{t("ownership")}</span>
              <select
                value={ownership}
                onChange={(event) =>
                  setOwnership(event.target.value as Ownership)
                }
              >
                <option value="platform_managed">
                  {t("ownershipModes.platform_managed")}
                </option>
                <option value="customer_owned">
                  {t("ownershipModes.customer_owned")}
                </option>
              </select>
            </label>
            <label className="field">
              <span>{t("displayName")}</span>
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                maxLength={160}
              />
            </label>
            <label className="field">
              <span>{t("sender")}</span>
              <input
                value={sender}
                onChange={(event) => setSender(event.target.value)}
                placeholder="+998901234567"
              />
            </label>
            {provider === "twilio" ? (
              <>
                <label className="field">
                  <span>{t("accountSid")}</span>
                  <input
                    value={accountSid}
                    onChange={(event) => setAccountSid(event.target.value)}
                    autoComplete="off"
                  />
                </label>
                <label className="field">
                  <span>{t("messagingServiceSid")}</span>
                  <input
                    value={messagingServiceSid}
                    onChange={(event) =>
                      setMessagingServiceSid(event.target.value)
                    }
                    autoComplete="off"
                  />
                </label>
              </>
            ) : null}
            {provider === "twilio" && ownership === "customer_owned" ? (
              <>
                <label className="field">
                  <span>{t("apiKeySid")}</span>
                  <input
                    value={apiKeySid}
                    onChange={(event) => setApiKeySid(event.target.value)}
                    autoComplete="off"
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
              </>
            ) : null}
          </div>
          <div className="write-only-note">{t("writeOnly")}</div>
          <button
            className="button primary"
            disabled={create.isPending || !sender}
            onClick={() => create.mutate()}
          >
            <Plus aria-hidden="true" />{" "}
            {create.isPending ? t("connecting") : t("connect")}
          </button>
        </section>
      ) : null}
      {rows.length ? (
        <section className="channel-grid" aria-label={t("listLabel")}>
          {rows.map((connection) => (
            <article className="channel-card" key={connection.id}>
              <div className="channel-card-heading">
                <div className="channel-icon sms-gradient">
                  <Smartphone aria-hidden="true" />
                </div>
                <div>
                  <h2>{connection.sender_address}</h2>
                  <StatusBadge status={connection.status} />
                </div>
              </div>
              <p>{t(`providers.${connection.provider}`)}</p>
              <dl>
                <div>
                  <dt>{t("health")}</dt>
                  <dd>
                    {smsNeedsAttention(connection)
                      ? t("attention")
                      : t("healthy")}
                  </dd>
                </div>
                <div>
                  <dt>{t("optOut")}</dt>
                  <dd>
                    {connection.advanced_opt_out_enabled
                      ? t("advanced")
                      : t("standard")}
                  </dd>
                </div>
                <div>
                  <dt>{t("automation")}</dt>
                  <dd>{t(`modes.${connection.ai_mode}`)}</dd>
                </div>
              </dl>
              <Link
                className="button secondary"
                href={`/app/settings/channels/sms/${connection.id}`}
              >
                {t("manage")} <ExternalLink aria-hidden="true" />
              </Link>
            </article>
          ))}
        </section>
      ) : !editable ? (
        <EmptyState
          icon={<Smartphone />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
        />
      ) : null}
    </>
  );
}
