"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ExternalLink,
  LockKeyhole,
  PhoneCall,
  Plus,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { voiceNeedsAttention } from "@/lib/voice";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function VoiceConnectionsPage() {
  const t = useTranslations("voice");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [carrier, setCarrier] = useState<"fake" | "twilio_sip">("fake");
  const [ownership, setOwnership] = useState<
    "platform_managed" | "customer_owned"
  >("platform_managed");
  const [phone, setPhone] = useState("+15550107777");
  const [displayName, setDisplayName] = useState("Main Voice AI");
  const [accountSid, setAccountSid] = useState("");
  const [trunkSid, setTrunkSid] = useState("");
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
    queryKey: ["voice", "readiness"],
    queryFn: () => workspace.api.voiceReadiness(),
  });
  const connections = useQuery({
    queryKey: ["voice", organizationId],
    queryFn: () => workspace.api.voiceConnections(),
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createVoiceConnection({
        display_name: displayName,
        carrier,
        ownership_mode: ownership,
        phone_number_e164: phone,
        carrier_account_sid: accountSid,
        sip_trunk_sid: trunkSid,
        carrier_api_key_sid: apiKeySid,
        carrier_api_key_secret: apiKeySecret,
        carrier_auth_token: authToken,
        ai_mode: "autopilot",
        disclosure_mode: "ai_and_transcript_disclosure",
        transcript_retention_mode: "30_days",
      }),
    onSuccess: async () => {
      setApiKeySecret("");
      setAuthToken("");
      setNotice(t("created"));
      await queryClient.invalidateQueries({
        queryKey: ["voice", organizationId],
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
      <div className="info-banner voice-policy-banner">
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
      <div className="voice-legal-warning" role="note">
        <strong>{t("legalTitle")}</strong>
        <p>{t("legalDescription")}</p>
      </div>
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      {editable && rows.length === 0 ? (
        <section className="panel voice-connect-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("setupEyebrow")}</span>
              <h2>{t("setupTitle")}</h2>
              <p>{t("setupDescription")}</p>
            </div>
            <PhoneCall aria-hidden="true" />
          </div>
          <div className="form-grid two-columns">
            <label className="field">
              <span>{t("carrier")}</span>
              <select
                value={carrier}
                onChange={(event) =>
                  setCarrier(event.target.value as typeof carrier)
                }
              >
                <option value="fake">{t("carriers.fake")}</option>
                <option
                  value="twilio_sip"
                  disabled={!readiness.data?.live_ready}
                >
                  {t("carriers.twilio_sip")}
                </option>
              </select>
            </label>
            <label className="field">
              <span>{t("ownership")}</span>
              <select
                value={ownership}
                onChange={(event) =>
                  setOwnership(event.target.value as typeof ownership)
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
              <span>{t("phoneNumber")}</span>
              <input
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                placeholder="+998901234567"
              />
            </label>
            {carrier === "twilio_sip" ? (
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
                  <span>{t("trunkSid")}</span>
                  <input
                    value={trunkSid}
                    onChange={(event) => setTrunkSid(event.target.value)}
                    autoComplete="off"
                  />
                </label>
              </>
            ) : null}
            {carrier === "twilio_sip" && ownership === "customer_owned" ? (
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
            disabled={create.isPending || !phone}
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
            <article
              className="channel-card voice-channel-card"
              key={connection.id}
            >
              <div className="channel-card-heading">
                <div className="channel-icon voice-gradient">
                  <PhoneCall aria-hidden="true" />
                </div>
                <div>
                  <h2>{connection.phone_number_e164}</h2>
                  <StatusBadge status={connection.status} />
                </div>
              </div>
              <p>{t(`carriers.${connection.carrier}`)}</p>
              <dl>
                <div>
                  <dt>{t("health")}</dt>
                  <dd>
                    {voiceNeedsAttention(connection)
                      ? t("attention")
                      : t("healthy")}
                  </dd>
                </div>
                <div>
                  <dt>{t("realtime")}</dt>
                  <dd>
                    {connection.health.realtime_ready
                      ? t("ready")
                      : t("unavailable")}
                  </dd>
                </div>
                <div>
                  <dt>{t("worker")}</dt>
                  <dd>
                    {connection.health.worker_ready
                      ? t("ready")
                      : t("unavailable")}
                  </dd>
                </div>
                <div>
                  <dt>{t("recording")}</dt>
                  <dd>{t("disabled")}</dd>
                </div>
              </dl>
              <Link
                className="button secondary"
                href={`/app/settings/channels/voice/${connection.id}`}
              >
                {t("manage")} <ExternalLink aria-hidden="true" />
              </Link>
            </article>
          ))}
        </section>
      ) : !editable ? (
        <EmptyState
          icon={<Activity />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
        />
      ) : null}
    </>
  );
}
