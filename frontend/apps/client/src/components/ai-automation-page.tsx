"use client";

import type { AIRuntimeConfig, AIToolPolicy } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Bot,
  CircleAlert,
  Gauge,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { canManageCrm } from "@/lib/crm";
import { aiQueryKeys } from "@/lib/ai-runtime";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

function count(record: Record<string, number> | undefined, key: string) {
  return record?.[key] ?? 0;
}

export function AIAutomationPage() {
  const t = useTranslations("aiAutomation");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const membership = workspace.membership!;
  const readOnly =
    !canManageCrm(membership.role) ||
    ["suspended", "archived"].includes(membership.organization_status);
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState<Partial<AIRuntimeConfig>>({});

  const config = useQuery({
    queryKey: aiQueryKeys.config(organizationId),
    queryFn: () => workspace.api.aiRuntimeConfig(),
  });
  const policies = useQuery({
    queryKey: aiQueryKeys.policies(organizationId),
    queryFn: () => workspace.api.aiToolPolicies(),
  });
  const usage = useQuery({
    queryKey: aiQueryKeys.usage(organizationId),
    queryFn: () => workspace.api.aiUsage(),
  });
  const runs = useQuery({
    queryKey: aiQueryKeys.runs(organizationId),
    queryFn: () => workspace.api.aiRuns({ page_size: 8 }),
    refetchInterval: 5000,
  });
  const channels = useQuery({
    queryKey: [...aiQueryKeys.root(organizationId), "channels"],
    queryFn: () => workspace.api.channels(),
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: aiQueryKeys.root(organizationId),
    });
  };
  const save = useMutation({
    mutationFn: () => workspace.api.updateAIRuntimeConfig(form),
    onSuccess: async () => {
      setForm({});
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const policyMutation = useMutation({
    mutationFn: (policy: AIToolPolicy) =>
      workspace.api.updateAIToolPolicies([
        {
          tool_name: policy.tool_name,
          enabled: policy.enabled,
          execution_mode: policy.enabled ? policy.execution_mode : "disabled",
          configuration: policy.configuration,
        },
      ]),
    onSuccess: async () => {
      setNotice(t("policySaved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  if (config.isLoading || policies.isLoading || usage.isLoading)
    return <PageSkeleton />;
  if (config.error || policies.error || usage.error)
    return (
      <ErrorState
        title={t("loadError")}
        description={
          (config.error ?? policies.error ?? usage.error)?.message ?? ""
        }
        onRetry={() => void refresh()}
      />
    );

  const eligibleChannels =
    channels.data?.results.filter(
      (item) =>
        (item.provider === "internal_test" && item.type === "webchat") ||
        (item.provider === "meta_instagram" && item.type === "instagram") ||
        (item.provider === "telegram_bot_api" && item.type === "telegram") ||
        (item.provider === "google_gmail" && item.type === "gmail") ||
        ((item.provider === "fake_sms" || item.provider === "twilio") &&
          item.type === "sms"),
    ) ?? [];
  const current = { ...config.data!, ...form } as AIRuntimeConfig;

  return (
    <div className="ai-automation-page">
      <PageHeading title={t("title")} description={t("description")} />

      <div className="ai-stage-warning" role="note">
        <CircleAlert aria-hidden="true" />
        <div>
          <strong>{t("stageWarningTitle")}</strong>
          <p>{t("stageWarning")}</p>
        </div>
      </div>
      {notice ? (
        <p className="form-notice" role="status">
          {notice}
        </p>
      ) : null}

      <section className="ai-settings-grid" aria-label={t("runtimeSection")}>
        <article className="settings-card ai-runtime-card">
          <header className="card-heading">
            <span className="card-icon">
              <Bot aria-hidden="true" />
            </span>
            <div>
              <small>{t("runtimeSection")}</small>
              <h2>{t("runtimeTitle")}</h2>
            </div>
            <StatusBadge status={current.enabled ? "enabled" : "disabled"} />
          </header>
          <div className="ai-form-grid">
            <label className="toggle-row">
              <span>
                <strong>{t("enableLabel")}</strong>
                <small>{t("enableHint")}</small>
              </span>
              <input
                type="checkbox"
                checked={Boolean(current.enabled)}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    enabled: event.target.checked,
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("defaultMode")}</span>
              <select
                value={current.default_mode ?? "off"}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    default_mode: event.target
                      .value as AIRuntimeConfig["default_mode"],
                  }))
                }
                disabled={readOnly}
              >
                <option value="off">{t("modes.off")}</option>
                <option value="suggest">{t("modes.suggest")}</option>
                <option value="autopilot_test">{t("modes.autopilot")}</option>
              </select>
            </label>
            <label className="field">
              <span>{t("modelAlias")}</span>
              <input
                value={current.model ?? ""}
                onChange={(event) =>
                  setForm((value) => ({ ...value, model: event.target.value }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("maxOutput")}</span>
              <input
                type="number"
                min={64}
                max={4000}
                value={current.max_output_tokens ?? 600}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    max_output_tokens: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("toolRounds")}</span>
              <input
                type="number"
                min={0}
                max={5}
                value={current.max_tool_rounds ?? 2}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    max_tool_rounds: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("timeout")}</span>
              <input
                type="number"
                min={2}
                max={120}
                value={current.timeout_seconds ?? 30}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    timeout_seconds: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("debounce")}</span>
              <input
                type="number"
                min={0}
                max={30}
                value={current.inbound_debounce_seconds ?? 0}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    inbound_debounce_seconds: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("dailyLimit")}</span>
              <input
                type="number"
                min={1}
                max={10000}
                value={current.daily_run_limit ?? 100}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    daily_run_limit: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("monthlyInputLimit")}</span>
              <input
                type="number"
                min={1000}
                max={100000000}
                value={current.monthly_input_token_limit ?? 500000}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    monthly_input_token_limit: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
            <label className="field">
              <span>{t("monthlyOutputLimit")}</span>
              <input
                type="number"
                min={1000}
                max={50000000}
                value={current.monthly_output_token_limit ?? 100000}
                onChange={(event) =>
                  setForm((value) => ({
                    ...value,
                    monthly_output_token_limit: Number(event.target.value),
                  }))
                }
                disabled={readOnly}
              />
            </label>
          </div>
          <div className="ai-requirements">
            <div>
              <ShieldCheck aria-hidden="true" />
              <span>{t("publishedContext")}</span>
              <strong>
                {current.published_context_version
                  ? `v${current.published_context_version}`
                  : t("missing")}
              </strong>
            </div>
            <div>
              <Sparkles aria-hidden="true" />
              <span>{t("provider")}</span>
              <strong>{t(`providerStatus.${current.provider_status}`)}</strong>
            </div>
            <div>
              <LockKeyhole aria-hidden="true" />
              <span>{t("credentials")}</span>
              <strong>{t("serverOnly")}</strong>
            </div>
          </div>
          <fieldset className="ai-channel-picker" disabled={readOnly}>
            <legend>{t("internalChannels")}</legend>
            {eligibleChannels.length ? (
              eligibleChannels.map((channel) => (
                <label key={channel.id}>
                  <input
                    type="checkbox"
                    checked={(
                      current.allowed_channel_connections ?? []
                    ).includes(channel.id)}
                    onChange={(event) =>
                      setForm((value) => ({
                        ...value,
                        allowed_channel_connections: event.target.checked
                          ? [
                              ...(current.allowed_channel_connections ?? []),
                              channel.id,
                            ]
                          : (current.allowed_channel_connections ?? []).filter(
                              (id) => id !== channel.id,
                            ),
                      }))
                    }
                  />
                  <span>
                    <strong>{channel.display_name}</strong>
                    <small>
                      {channel.provider} · {channel.status}
                    </small>
                  </span>
                </label>
              ))
            ) : (
              <p>{t("noInternalChannel")}</p>
            )}
          </fieldset>
          {!readOnly ? (
            <button
              className="button primary"
              onClick={() => save.mutate()}
              disabled={save.isPending}
            >
              {t("save")}
            </button>
          ) : null}
        </article>

        <article className="settings-card ai-usage-card">
          <header className="card-heading">
            <span className="card-icon">
              <Gauge aria-hidden="true" />
            </span>
            <div>
              <small>{t("usage")}</small>
              <h2>{t("currentUsage")}</h2>
            </div>
          </header>
          <div className="ai-metric-grid">
            <div>
              <span>{t("runsToday")}</span>
              <strong>{usage.data?.daily_runs ?? 0}</strong>
              <small>{t("ofLimit", { limit: current.daily_run_limit })}</small>
            </div>
            <div>
              <span>{t("inputTokens")}</span>
              <strong>
                {(usage.data?.monthly_input_tokens ?? 0).toLocaleString(locale)}
              </strong>
              <small>
                {t("ofLimit", {
                  limit:
                    current.monthly_input_token_limit.toLocaleString(locale),
                })}
              </small>
            </div>
            <div>
              <span>{t("outputTokens")}</span>
              <strong>
                {(usage.data?.monthly_output_tokens ?? 0).toLocaleString(
                  locale,
                )}
              </strong>
              <small>
                {t("ofLimit", {
                  limit:
                    current.monthly_output_token_limit.toLocaleString(locale),
                })}
              </small>
            </div>
            <div>
              <span>{t("handoffs")}</span>
              <strong>{count(usage.data?.outcome_counts, "handoff")}</strong>
              <small>{t("realStoredRuns")}</small>
            </div>
          </div>
          <div className="ai-outcome-strip">
            <span>
              {t("completed")}{" "}
              <strong>{count(usage.data?.status_counts, "completed")}</strong>
            </span>
            <span>
              {t("failed")}{" "}
              <strong>{count(usage.data?.status_counts, "failed")}</strong>
            </span>
            <span>
              {t("drafts")}{" "}
              <strong>{count(usage.data?.outcome_counts, "draft")}</strong>
            </span>
          </div>
        </article>
      </section>

      <section className="settings-card ai-tools-card">
        <header className="card-heading">
          <span className="card-icon">
            <Wrench aria-hidden="true" />
          </span>
          <div>
            <small>{t("permissions")}</small>
            <h2>{t("toolsTitle")}</h2>
          </div>
        </header>
        <p>{t("toolsDescription")}</p>
        <div className="ai-tool-list">
          {policies.data?.map((policy) => (
            <article key={policy.id}>
              <div>
                <strong>{policy.tool_name}</strong>
                <p>{policy.description}</p>
                <small>
                  {policy.mutating ? t("mutating") : t("readOnlyTool")} · v
                  {policy.version}
                </small>
              </div>
              <label className="sr-only" htmlFor={`tool-enabled-${policy.id}`}>
                {t("toolEnabled", { tool: policy.tool_name })}
              </label>
              <input
                id={`tool-enabled-${policy.id}`}
                type="checkbox"
                checked={policy.enabled}
                disabled={
                  readOnly ||
                  policy.tool_name === "request_human_handoff" ||
                  policyMutation.isPending
                }
                onChange={(event) =>
                  policyMutation.mutate({
                    ...policy,
                    enabled: event.target.checked,
                    execution_mode: event.target.checked
                      ? policy.mutating
                        ? "require_approval"
                        : "automatic"
                      : "disabled",
                  })
                }
              />
              <label className="field compact">
                <span>{t("execution")}</span>
                <select
                  value={policy.execution_mode}
                  disabled={
                    readOnly ||
                    !policy.enabled ||
                    policy.tool_name === "request_human_handoff" ||
                    policyMutation.isPending
                  }
                  onChange={(event) =>
                    policyMutation.mutate({
                      ...policy,
                      execution_mode: event.target
                        .value as AIToolPolicy["execution_mode"],
                    })
                  }
                >
                  <option value="automatic">{t("automatic")}</option>
                  <option value="require_approval">
                    {t("requireApproval")}
                  </option>
                  <option value="disabled">{t("disabled")}</option>
                </select>
              </label>
            </article>
          ))}
        </div>
      </section>

      <section className="settings-card ai-runs-card">
        <header className="card-heading">
          <span className="card-icon">
            <Activity aria-hidden="true" />
          </span>
          <div>
            <small>{t("trace")}</small>
            <h2>{t("recentRuns")}</h2>
          </div>
        </header>
        <p>{t("tracePrivacy")}</p>
        <div className="ai-run-list">
          {runs.data?.results.length ? (
            runs.data.results.map((run) => (
              <details key={run.id}>
                <summary>
                  <span>
                    <strong>{run.outcome || run.status}</strong>
                    <small>
                      {new Intl.DateTimeFormat(locale, {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }).format(new Date(run.created_at))}
                    </small>
                  </span>
                  <StatusBadge status={run.status} />
                </summary>
                <dl>
                  <div>
                    <dt>{t("runId")}</dt>
                    <dd>{run.id}</dd>
                  </div>
                  <div>
                    <dt>{t("contextVersion")}</dt>
                    <dd>{run.ai_context_revision}</dd>
                  </div>
                  <div>
                    <dt>{t("promptVersion")}</dt>
                    <dd>{run.prompt_template_version}</dd>
                  </div>
                  <div>
                    <dt>{t("promptHash")}</dt>
                    <dd>{run.prompt_hash}</dd>
                  </div>
                  <div>
                    <dt>{t("tokens")}</dt>
                    <dd>
                      {run.input_tokens} / {run.output_tokens}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("latency")}</dt>
                    <dd>{run.latency_ms} ms</dd>
                  </div>
                </dl>
                {run.tool_calls.length ? (
                  <ul>
                    {run.tool_calls.map((tool) => (
                      <li key={tool.id}>
                        <strong>{tool.tool_name}</strong> — {tool.status}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {run.error_code ? (
                  <p className="run-error">
                    <CircleAlert aria-hidden="true" />
                    {run.error_category}: {run.error_code}
                  </p>
                ) : null}
              </details>
            ))
          ) : (
            <p className="empty-inline">{t("noRuns")}</p>
          )}
        </div>
      </section>
    </div>
  );
}
