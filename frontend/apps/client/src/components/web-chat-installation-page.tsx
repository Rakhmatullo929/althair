"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, ExternalLink, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

export function WebChatInstallationPage({
  installationId,
}: {
  installationId: string;
}) {
  const t = useTranslations("webChat");
  const workspace = useWorkspace();
  const cache = useQueryClient();
  const readOnly =
    !can(workspace.membership?.role, "manage_channels") ||
    ["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const [copied, setCopied] = useState(false);
  const [form, setForm] = useState({
    display_name: "",
    assistant_label: "",
    greeting: "",
    offline_message: "",
    human_handoff_message: "",
    privacy_policy_url: "",
    terms_url: "",
    consent_text: "",
    allowed_origins: "",
    default_language: "ru",
    default_branch: "",
    ai_mode: "manual",
    retention_days: 30,
    launcher_position: "right",
    require_consent: true,
    require_prechat_form: false,
    collect_name: true,
    collect_email: false,
    collect_phone: false,
  });
  const query = useQuery({
    queryKey: ["web-chat-installation", installationId],
    queryFn: () => workspace.api.webChatInstallation(installationId),
  });
  const sessions = useQuery({
    queryKey: ["web-chat-sessions", installationId],
    queryFn: () => workspace.api.webChatSessions(installationId),
  });
  const metrics = useQuery({
    queryKey: ["web-chat-metrics", installationId],
    queryFn: () => workspace.api.webChatMetrics(installationId),
  });
  const branches = useQuery({
    queryKey: ["branches", workspace.selectedOrganizationId],
    queryFn: () => workspace.api.branches(workspace.selectedOrganizationId!),
  });
  useEffect(() => {
    if (query.data) {
      // Query data is the authoritative server snapshot after save/actions.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setForm({
        display_name: query.data.display_name,
        assistant_label: query.data.assistant_label,
        greeting: query.data.greeting,
        offline_message: query.data.offline_message,
        human_handoff_message: query.data.human_handoff_message,
        privacy_policy_url: query.data.privacy_policy_url,
        terms_url: query.data.terms_url,
        consent_text: query.data.consent_text,
        allowed_origins: query.data.allowed_origins.join("\n"),
        default_language: query.data.default_language,
        default_branch: query.data.default_branch ?? "",
        ai_mode: query.data.ai_mode,
        retention_days: query.data.retention_days,
        launcher_position: query.data.theme_config.position,
        require_consent: query.data.require_consent,
        require_prechat_form: query.data.require_prechat_form,
        collect_name: query.data.collect_name,
        collect_email: query.data.collect_email,
        collect_phone: query.data.collect_phone,
      });
    }
  }, [query.data]);
  const refresh = async () => {
    await Promise.all([
      cache.invalidateQueries({
        queryKey: ["web-chat-installation", installationId],
      }),
      cache.invalidateQueries({ queryKey: ["web-chat-installations"] }),
    ]);
  };
  const save = useMutation({
    mutationFn: () => {
      const { launcher_position, ...values } = form;
      return workspace.api.updateWebChatInstallation(installationId, {
        ...values,
        default_branch: form.default_branch || null,
        default_language: form.default_language as "ru" | "uz" | "en",
        ai_mode: form.ai_mode as "manual" | "suggest" | "autopilot",
        theme_config: {
          ...(query.data?.theme_config ?? {
            accent: "emerald",
            radius: "rounded",
          }),
          position: launcher_position,
        },
        allowed_origins: form.allowed_origins
          .split(/\n|,/)
          .map((value) => value.trim())
          .filter(Boolean),
      });
    },
    onSuccess: refresh,
  });
  const action = useMutation({
    mutationFn: (value: "activate" | "pause" | "revoke" | "rotate-key") =>
      workspace.api.webChatInstallationAction(installationId, value),
    onSuccess: refresh,
  });
  const anonymize = useMutation({
    mutationFn: (sessionId: string) =>
      workspace.api.anonymizeWebChatSession(installationId, sessionId),
    onSuccess: async () =>
      cache.invalidateQueries({
        queryKey: ["web-chat-sessions", installationId],
      }),
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return (
      <ErrorState
        title={t("error")}
        description={(query.error as Error)?.message ?? t("notFound")}
        onRetry={() => void query.refetch()}
      />
    );
  const item = query.data;
  return (
    <>
      <Link className="back-button" href="/app/settings/channels/web-chat">
        ← {t("back")}
      </Link>
      <PageHeading
        title={item.display_name}
        description={t("detailDescription")}
      />
      <div className="webchat-status-strip">
        <StatusBadge status={item.status} />
        <span>
          {item.health.published_context
            ? t("contextReady")
            : t("contextMissing")}
        </span>
        <span>
          {item.health.public_api_enabled ? t("apiEnabled") : t("apiDisabled")}
        </span>
      </div>
      <section className="panel webchat-settings-panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("configuration")}</span>
            <h2>{t("visitorExperience")}</h2>
          </div>
          <ShieldCheck />
        </div>
        <div className="form-grid two">
          {(
            [
              "display_name",
              "assistant_label",
              "greeting",
              "offline_message",
              "human_handoff_message",
              "privacy_policy_url",
              "terms_url",
              "consent_text",
            ] as const
          ).map((key) => (
            <label
              className={`field ${["greeting", "offline_message", "human_handoff_message", "consent_text"].includes(key) ? "field-span" : ""}`}
              key={key}
            >
              <span>{key === "display_name" ? t("displayName") : t(key)}</span>
              {[
                "greeting",
                "offline_message",
                "human_handoff_message",
                "consent_text",
              ].includes(key) ? (
                <textarea
                  rows={3}
                  value={form[key]}
                  disabled={readOnly}
                  onChange={(event) =>
                    setForm({ ...form, [key]: event.target.value })
                  }
                />
              ) : (
                <input
                  type={key.endsWith("_url") ? "url" : "text"}
                  value={form[key]}
                  disabled={readOnly}
                  onChange={(event) =>
                    setForm({ ...form, [key]: event.target.value })
                  }
                />
              )}
            </label>
          ))}
          <label className="field field-span">
            <span>{t("allowedOrigins")}</span>
            <textarea
              rows={3}
              value={form.allowed_origins}
              disabled={readOnly}
              onChange={(event) =>
                setForm({ ...form, allowed_origins: event.target.value })
              }
            />
            <small>{t("exactOrigins")}</small>
          </label>
          <label className="field">
            <span>{t("language")}</span>
            <select
              value={form.default_language}
              disabled={readOnly}
              onChange={(event) =>
                setForm({ ...form, default_language: event.target.value })
              }
            >
              <option value="ru">Русский</option>
              <option value="uz">O‘zbekcha</option>
              <option value="en">English</option>
            </select>
          </label>
          <label className="field">
            <span>{t("defaultBranch")}</span>
            <select
              value={form.default_branch}
              disabled={readOnly}
              onChange={(event) =>
                setForm({ ...form, default_branch: event.target.value })
              }
            >
              <option value="">{t("allBranches")}</option>
              {branches.data?.results
                .filter((branch) => branch.is_active)
                .map((branch) => (
                  <option value={branch.id} key={branch.id}>
                    {branch.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>{t("aiMode")}</span>
            <select
              value={form.ai_mode}
              disabled={readOnly}
              onChange={(event) =>
                setForm({ ...form, ai_mode: event.target.value })
              }
            >
              <option value="manual">{t("manual")}</option>
              <option value="suggest">{t("suggest")}</option>
              <option value="autopilot">{t("autopilot")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("launcherPosition")}</span>
            <select
              value={form.launcher_position}
              disabled={readOnly}
              onChange={(event) =>
                setForm({ ...form, launcher_position: event.target.value })
              }
            >
              <option value="right">{t("right")}</option>
              <option value="left">{t("left")}</option>
            </select>
          </label>
          <label className="field">
            <span>{t("retention")}</span>
            <input
              type="number"
              min={1}
              max={365}
              value={form.retention_days}
              disabled={readOnly}
              onChange={(event) =>
                setForm({ ...form, retention_days: Number(event.target.value) })
              }
            />
          </label>
          <div className="webchat-checks">
            {(
              [
                "require_consent",
                "require_prechat_form",
                "collect_name",
                "collect_email",
                "collect_phone",
              ] as const
            ).map((key) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={form[key]}
                  disabled={readOnly}
                  onChange={(event) =>
                    setForm({ ...form, [key]: event.target.checked })
                  }
                />
                {t(key)}
              </label>
            ))}
          </div>
        </div>
        {save.error || action.error ? (
          <div className="form-alert">
            {((save.error || action.error) as Error).message}
          </div>
        ) : null}
        {!readOnly ? (
          <div className="form-actions">
            <button
              className="button primary"
              disabled={save.isPending}
              onClick={() => save.mutate()}
            >
              {t("save")}
            </button>
            {item.status !== "active" ? (
              <button
                className="button secondary"
                onClick={() => action.mutate("activate")}
              >
                {t("activate")}
              </button>
            ) : (
              <button
                className="button secondary"
                onClick={() => action.mutate("pause")}
              >
                {t("pause")}
              </button>
            )}
            <button
              className="button secondary"
              onClick={() => action.mutate("rotate-key")}
            >
              {t("rotateKey")}
            </button>
            <button
              className="button danger-ghost"
              onClick={() => action.mutate("revoke")}
            >
              {t("revoke")}
            </button>
          </div>
        ) : null}
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("embed")}</span>
            <h2>{t("installCode")}</h2>
          </div>
          <Link
            className="button secondary"
            href={`/demo?installation=${item.public_key}`}
          >
            <ExternalLink />
            {t("openDemo")}
          </Link>
        </div>
        <pre className="embed-code">
          <code>{item.embed_snippet}</code>
        </pre>
        <button
          className="button secondary"
          onClick={() =>
            void navigator.clipboard.writeText(item.embed_snippet).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 1500);
            })
          }
        >
          {copied ? <Check /> : <Copy />}
          {copied ? t("copied") : t("copy")}
        </button>
      </section>
      <section className="webchat-ops-grid">
        <div className="panel">
          <h2>{t("metrics")}</h2>
          <dl className="webchat-facts">
            {Object.entries(metrics.data?.events ?? {}).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </div>
        <div className="panel">
          <h2>{t("recentSessions")}</h2>
          <ul className="webchat-session-list">
            {sessions.data?.results.slice(0, 8).map((session) => (
              <li key={session.public_session_id}>
                <div>
                  <strong>{session.status}</strong>
                  <span>
                    {session.language} ·{" "}
                    {new Date(session.started_at).toLocaleString()}
                  </span>
                </div>
                <button
                  className="text-button"
                  disabled={readOnly}
                  onClick={() => anonymize.mutate(session.public_session_id)}
                >
                  {t("anonymize")}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
