"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Locale, VoiceConnection } from "@workspace/api-client";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Bot,
  Clock3,
  Headphones,
  Pause,
  PhoneCall,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Unplug,
  UserRoundCheck,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { formatCallDuration, transcriptVisible } from "@/lib/voice";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function VoiceConnectionPage({
  connectionId,
}: {
  connectionId: string;
}) {
  const t = useTranslations("voice");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [notice, setNotice] = useState("");
  const [destinationKey, setDestinationKey] = useState("front-desk");
  const [destinationName, setDestinationName] = useState("Front desk");
  const [destinationType, setDestinationType] = useState<"phone" | "sip">(
    "phone",
  );
  const [destination, setDestination] = useState("+15550103333");
  const [testLanguage, setTestLanguage] = useState<Locale>("ru");
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const connection = useQuery({
    queryKey: ["voice", organizationId, connectionId],
    queryFn: () => workspace.api.voiceConnection(connectionId),
  });
  const calls = useQuery({
    queryKey: ["voice", "calls", organizationId],
    queryFn: () => workspace.api.voiceCalls(),
  });
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["voice", organizationId] }),
      queryClient.invalidateQueries({
        queryKey: ["voice", "calls", organizationId],
      }),
    ]);
  };
  const action = useMutation({
    mutationFn: (name: "pause" | "activate" | "disconnect") =>
      workspace.api.voiceAction(connectionId, name),
    onSuccess: async () => {
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const health = useMutation({
    mutationFn: () => workspace.api.voiceHealth(connectionId, true),
    onSuccess: async () => {
      setNotice(t("healthRefreshed"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const save = useMutation({
    mutationFn: (
      body: Parameters<typeof workspace.api.updateVoiceConnection>[1],
    ) => workspace.api.updateVoiceConnection(connectionId, body),
    onSuccess: async () => {
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const addTransfer = useMutation({
    mutationFn: () =>
      workspace.api.createVoiceTransfer(connectionId, {
        key: destinationKey,
        display_name: destinationName,
        destination_type: destinationType,
        destination,
        fallback_behavior: "callback_task",
      }),
    onSuccess: async () => {
      setDestination("");
      setNotice(t("destinationCreated"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const testCall = useMutation({
    mutationFn: () =>
      workspace.api.voiceTestCall(connectionId, {
        caller: "+15550104444",
        language: testLanguage,
        utterance:
          testLanguage === "ru"
            ? "Расскажите о ваших услугах"
            : testLanguage === "uz"
              ? "Xizmatlaringiz haqida ayting"
              : "Tell me about your services",
      }),
    onSuccess: async () => {
      setNotice(t("testCompleted"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  if (connection.isLoading || calls.isLoading) return <PageSkeleton />;
  if (connection.error || calls.error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={((connection.error ?? calls.error) as Error).message}
        onRetry={() => void connection.refetch()}
      />
    );
  const row = connection.data!;
  const recentCalls = (calls.data?.results ?? []).filter(
    (call) => call.voice_connection === connectionId,
  );
  return (
    <>
      <Link className="back-link" href="/app/settings/channels/voice">
        <ArrowLeft aria-hidden="true" /> {t("back")}
      </Link>
      <PageHeading
        title={row.phone_number_e164}
        description={`${t(`carriers.${row.carrier}`)} · ${t(`modes.${row.ai_mode}`)}`}
        actions={<StatusBadge status={row.status} />}
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}
      <section className="voice-health-grid" aria-label={t("health")}>
        <article className="voice-health-card">
          <Activity />
          <span>{t("carrierHealth")}</span>
          <strong>
            {row.health.carrier_reachable === false
              ? t("unavailable")
              : t("ready")}
          </strong>
        </article>
        <article className="voice-health-card">
          <Headphones />
          <span>{t("realtime")}</span>
          <strong>
            {row.health.realtime_ready ? t("ready") : t("unavailable")}
          </strong>
        </article>
        <article className="voice-health-card">
          <RefreshCw />
          <span>{t("worker")}</span>
          <strong>
            {row.health.worker_ready ? t("ready") : t("unavailable")}
          </strong>
        </article>
        <article className="voice-health-card">
          <ShieldCheck />
          <span>{t("recording")}</span>
          <strong>{t("disabled")}</strong>
        </article>
      </section>
      <section className="panel voice-settings-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("behaviorEyebrow")}</span>
            <h2>{t("behaviorTitle")}</h2>
            <p>{t("behaviorDescription")}</p>
          </div>
          <Bot />
        </div>
        <div className="form-grid two-columns">
          <label className="field">
            <span>{t("aiMode")}</span>
            <select
              value={row.ai_mode}
              disabled={!editable}
              onChange={(event) =>
                save.mutate({
                  ai_mode: event.target.value as VoiceConnection["ai_mode"],
                })
              }
            >
              <option value="manual">{t("modes.manual")}</option>
              <option value="suggest">{t("modes.suggest")}</option>
              <option value="autopilot">{t("modes.autopilot")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("voiceName")}</span>
            <select
              value={row.voice_name}
              disabled={!editable}
              onChange={(event) =>
                save.mutate({ voice_name: event.target.value })
              }
            >
              <option value="marin">Marin</option>
              <option value="cedar">Cedar</option>
            </select>
          </label>
          <label className="field">
            <span>{t("reasoning")}</span>
            <select
              value={row.reasoning_effort}
              disabled={!editable}
              onChange={(event) =>
                save.mutate({
                  reasoning_effort: event.target
                    .value as VoiceConnection["reasoning_effort"],
                })
              }
            >
              <option value="low">{t("low")}</option>
              <option value="medium">{t("medium")}</option>
              <option value="high">{t("high")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("defaultLanguage")}</span>
            <select
              value={row.default_language}
              disabled={!editable}
              onChange={(event) =>
                save.mutate({ default_language: event.target.value as Locale })
              }
            >
              <option value="ru">RU</option>
              <option value="uz">UZ</option>
              <option value="en">EN</option>
            </select>
          </label>
          <label className="field field-span">
            <span>{t("greeting")}</span>
            <textarea
              rows={3}
              defaultValue={row.greeting}
              disabled={!editable}
              onBlur={(event) =>
                event.target.value !== row.greeting &&
                save.mutate({ greeting: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>{t("disclosure")}</span>
            <select
              value={row.disclosure_mode}
              disabled={!editable}
              onChange={(event) =>
                save.mutate({
                  disclosure_mode: event.target
                    .value as VoiceConnection["disclosure_mode"],
                })
              }
            >
              <option value="ai_disclosure">
                {t("disclosures.ai_disclosure")}
              </option>
              <option value="ai_and_transcript_disclosure">
                {t("disclosures.ai_and_transcript_disclosure")}
              </option>
              <option value="explicit_transcript_consent">
                {t("disclosures.explicit_transcript_consent")}
              </option>
            </select>
          </label>
          <label className="field">
            <span>{t("retention")}</span>
            <select
              value={row.transcript_retention_mode}
              disabled={!editable}
              onChange={(event) =>
                save.mutate({
                  transcript_retention_mode: event.target
                    .value as VoiceConnection["transcript_retention_mode"],
                })
              }
            >
              <option value="disabled">{t("retentions.disabled")}</option>
              <option value="30_days">{t("retentions.30_days")}</option>
              <option value="90_days">{t("retentions.90_days")}</option>
              <option value="indefinite">{t("retentions.indefinite")}</option>
            </select>
          </label>
        </div>
        <div className="voice-policy-note">
          <AlertTriangle />
          <div>
            <strong>{t("legalTitle")}</strong>
            <p>{t("legalDescription")}</p>
          </div>
        </div>
      </section>
      <section className="panel voice-limits-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("limitsEyebrow")}</span>
            <h2>{t("limitsTitle")}</h2>
          </div>
          <Clock3 />
        </div>
        <div className="voice-metric-grid">
          <div>
            <span>{t("maxDuration")}</span>
            <strong>{formatCallDuration(row.max_call_seconds)}</strong>
          </div>
          <div>
            <span>{t("concurrentCalls")}</span>
            <strong>
              {row.health.active_calls}/{row.max_concurrent_calls}
            </strong>
          </div>
          <div>
            <span>{t("dailyMinutes")}</span>
            <strong>{row.daily_minute_limit}</strong>
          </div>
          <div>
            <span>{t("monthlyMinutes")}</span>
            <strong>{row.monthly_minute_limit}</strong>
          </div>
        </div>
      </section>
      <section className="panel voice-transfer-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("transfersEyebrow")}</span>
            <h2>{t("transfersTitle")}</h2>
            <p>{t("transfersDescription")}</p>
          </div>
          <UserRoundCheck />
        </div>
        {row.transfer_destinations.length ? (
          <div className="voice-transfer-list">
            {row.transfer_destinations.map((item) => (
              <article key={item.id}>
                <div>
                  <strong>{item.display_name}</strong>
                  <small>
                    {item.key} · {item.destination_type}
                  </small>
                </div>
                <StatusBadge status={item.active ? "active" : "paused"} />
                <span>{t("destinationProtected")}</span>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<UserRoundCheck />}
            title={t("noDestinations")}
            description={t("noDestinationsHint")}
          />
        )}
        {editable ? (
          <div className="form-grid four-columns">
            <label className="field">
              <span>{t("destinationKey")}</span>
              <input
                value={destinationKey}
                onChange={(event) => setDestinationKey(event.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("destinationName")}</span>
              <input
                value={destinationName}
                onChange={(event) => setDestinationName(event.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("destinationType")}</span>
              <select
                value={destinationType}
                onChange={(event) =>
                  setDestinationType(
                    event.target.value as typeof destinationType,
                  )
                }
              >
                <option value="phone">{t("phone")}</option>
                <option value="sip">SIP</option>
              </select>
            </label>
            <label className="field">
              <span>{t("destination")}</span>
              <input
                type="password"
                value={destination}
                autoComplete="new-password"
                onChange={(event) => setDestination(event.target.value)}
              />
            </label>
            <button
              className="button secondary"
              disabled={!destination || addTransfer.isPending}
              onClick={() => addTransfer.mutate()}
            >
              <Plus /> {t("addDestination")}
            </button>
          </div>
        ) : null}
        <div className="write-only-note">{t("destinationWriteOnly")}</div>
      </section>
      <section className="panel voice-test-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("testEyebrow")}</span>
            <h2>{t("testTitle")}</h2>
            <p>{t("testDescription")}</p>
          </div>
          <PhoneCall />
        </div>
        <div className="voice-test-controls">
          <select
            aria-label={t("defaultLanguage")}
            value={testLanguage}
            onChange={(event) => setTestLanguage(event.target.value as Locale)}
          >
            <option value="ru">RU</option>
            <option value="uz">UZ</option>
            <option value="en">EN</option>
          </select>
          <button
            className="button primary"
            disabled={!editable || row.carrier !== "fake" || testCall.isPending}
            onClick={() => testCall.mutate()}
          >
            <Play /> {testCall.isPending ? t("testing") : t("runTest")}
          </button>
        </div>
      </section>
      <section className="panel voice-calls-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("callsEyebrow")}</span>
            <h2>{t("callsTitle")}</h2>
          </div>
          <PhoneCall />
        </div>
        {recentCalls.length ? (
          <div className="voice-call-list">
            {recentCalls.map((call) => (
              <details
                key={call.id}
                className={`voice-call-card state-${call.status}`}
              >
                <summary>
                  <div>
                    <strong>{call.caller}</strong>
                    <small>
                      {new Intl.DateTimeFormat(locale, {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }).format(new Date(call.created_at))}
                    </small>
                  </div>
                  <StatusBadge status={call.status} />
                  <span>{formatCallDuration(call.duration_seconds)}</span>
                </summary>
                <div className="voice-call-detail">
                  <div className="voice-call-summary">
                    <strong>{t("summary")}</strong>
                    <p>{call.summary || t("noSummary")}</p>
                    <dl>
                      <div>
                        <dt>{t("language")}</dt>
                        <dd>{call.selected_language || "—"}</dd>
                      </div>
                      <div>
                        <dt>{t("outcome")}</dt>
                        <dd>{call.outcome || "—"}</dd>
                      </div>
                      <div>
                        <dt>{t("consent")}</dt>
                        <dd>{call.consent_state}</dd>
                      </div>
                      <div>
                        <dt>{t("transfer")}</dt>
                        <dd>{call.transfer_status || "—"}</dd>
                      </div>
                    </dl>
                  </div>
                  {transcriptVisible(call) ? (
                    <ol className="voice-transcript">
                      {call.transcript.map((segment) => (
                        <li
                          key={segment.id}
                          className={`speaker-${segment.speaker}`}
                        >
                          <strong>{t(`speakers.${segment.speaker}`)}</strong>
                          <p>{segment.text}</p>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <div className="voice-transcript-hidden">
                      <ShieldCheck />
                      <p>{t("transcriptHidden")}</p>
                    </div>
                  )}
                  {call.tools.length ? (
                    <div className="voice-tool-list">
                      <strong>{t("tools")}</strong>
                      {call.tools.map((tool) => (
                        <span key={tool.id}>
                          {tool.name} · {tool.status}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              </details>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<PhoneCall />}
            title={t("noCalls")}
            description={t("noCallsHint")}
          />
        )}
      </section>
      <section className="voice-actions">
        <button
          className="button secondary"
          disabled={!editable || health.isPending}
          onClick={() => health.mutate()}
        >
          <RefreshCw /> {t("refreshHealth")}
        </button>
        {row.status === "paused" ? (
          <button
            className="button secondary"
            disabled={!editable}
            onClick={() => action.mutate("activate")}
          >
            <Play /> {t("activate")}
          </button>
        ) : (
          <button
            className="button secondary"
            disabled={!editable}
            onClick={() => action.mutate("pause")}
          >
            <Pause /> {t("pause")}
          </button>
        )}
        <button
          className="button danger"
          disabled={!editable}
          onClick={() => action.mutate("disconnect")}
        >
          <Unplug /> {t("disconnect")}
        </button>
      </section>
    </>
  );
}
