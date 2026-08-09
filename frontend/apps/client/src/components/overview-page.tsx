"use client";

import { useQuery } from "@tanstack/react-query";
import { Bot, Building2, Check, GitBranch, Radio, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

export function OverviewPage() {
  const t = useTranslations("overview");
  const workspace = useWorkspace();
  const id = workspace.selectedOrganizationId!;
  const query = useQuery({
    queryKey: ["overview", id],
    queryFn: () => workspace.api.overview(id),
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
  const data = query.data!;
  const cards = [
    ["company", `${data.onboarding_completion_percentage}%`, Building2],
    ["branches", String(data.branch_count), GitBranch],
    ["members", String(data.active_member_count), Users],
    ["channels", String(data.configured_channel_count), Radio],
  ] as const;
  const checklist = [
    [
      "company",
      data.onboarding_completion_percentage >= 20,
      "/app/settings/company",
    ],
    ["branch", data.branch_count > 0, "/app/settings/branches"],
    ["team", data.active_member_count > 1, "/app/settings/team"],
    [
      "context",
      data.ai_context_status === "published",
      "/app/settings/ai-context",
    ],
  ] as const;
  return (
    <>
      <PageHeading
        title={t("title")}
        description={t("description")}
        actions={
          <Link className="button primary" href="/app/onboarding">
            {data.onboarding_completed_at
              ? t("reviewSetup")
              : t("continueSetup")}
          </Link>
        }
      />
      <section className="welcome-strip">
        <div>
          <span className="eyebrow">{t("workspaceHealth")}</span>
          <h2>{workspace.membership?.organization_name}</h2>
          <p>
            {t("progressSummary", {
              progress: data.onboarding_completion_percentage,
            })}
          </p>
        </div>
        <div
          className="progress-orbit"
          style={
            {
              "--progress": `${data.onboarding_completion_percentage * 3.6}deg`,
            } as React.CSSProperties
          }
        >
          <strong>{data.onboarding_completion_percentage}%</strong>
        </div>
      </section>
      <section className="metric-grid" aria-label={t("metricsLabel")}>
        {cards.map(([key, value, Icon]) => (
          <article className="metric-card" key={key}>
            <div className="metric-icon">
              <Icon aria-hidden="true" />
            </div>
            <div>
              <span>{t(`metrics.${key}`)}</span>
              <strong>{value}</strong>
            </div>
          </article>
        ))}
        <article className="metric-card">
          <div className="metric-icon">
            <Bot aria-hidden="true" />
          </div>
          <div>
            <span>{t("metrics.aiContext")}</span>
            <strong>
              <StatusBadge status={data.ai_context_status} />
            </strong>
            <small>{t("version", { version: data.ai_context_version })}</small>
          </div>
        </article>
      </section>
      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("checklistEyebrow")}</span>
              <h2>{t("checklistTitle")}</h2>
            </div>
          </div>
          <ul className="checklist">
            {checklist.map(([key, done, href]) => (
              <li key={key} className={done ? "done" : undefined}>
                <span className="check-circle">
                  <Check aria-hidden="true" />
                </span>
                <div>
                  <strong>{t(`checklist.${key}.title`)}</strong>
                  <p>{t(`checklist.${key}.description`)}</p>
                </div>
                <Link href={href}>{done ? t("review") : t("complete")}</Link>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("activityEyebrow")}</span>
              <h2>{t("activityTitle")}</h2>
            </div>
          </div>
          {data.recent_activity.length ? (
            <ul className="activity-list">
              {data.recent_activity.map((item) => (
                <li key={`${item.version}-${item.at}`}>
                  <Bot aria-hidden="true" />
                  <div>
                    <strong>
                      {t("publishedVersion", { version: item.version })}
                    </strong>
                    <p>{t("publishedBy", { actor: item.actor })}</p>
                    <time dateTime={item.at}>
                      {new Intl.DateTimeFormat(undefined, {
                        dateStyle: "medium",
                        timeStyle: "short",
                      }).format(new Date(item.at))}
                    </time>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <div className="compact-empty">
              <p>{t("noActivity")}</p>
            </div>
          )}
        </section>
      </div>
    </>
  );
}
