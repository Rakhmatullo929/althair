"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  CheckCircle2,
  CircleAlert,
  ContactRound,
  Inbox,
  ListTodo,
  Radio,
  UserRoundX,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { crmQueryKeys } from "@/lib/crm";
import { Link } from "@/i18n/navigation";
import { formatDateTime } from "./crm-shared";
import { useWorkspace } from "./workspace-provider";
import { ErrorState, PageHeading, PageSkeleton, StatusBadge } from "./ui";

export function OverviewPage() {
  const t = useTranslations("crm");
  const locale = useLocale();
  const workspace = useWorkspace();
  const organizationId = workspace.selectedOrganizationId!;
  const overview = useQuery({
    queryKey: crmQueryKeys.overview(organizationId),
    queryFn: () => workspace.api.crmOverview(),
  });
  const activity = useQuery({
    queryKey: crmQueryKeys.activity(organizationId),
    queryFn: () => workspace.api.crmActivity({ page_size: 8 }),
  });
  if (overview.isLoading) return <PageSkeleton />;
  if (overview.error)
    return (
      <ErrorState
        title={t("common.error")}
        description={(overview.error as Error).message}
        onRetry={() => void overview.refetch()}
      />
    );
  const data = overview.data!;
  const cards = [
    ["openConversations", data.open_conversations, Inbox, "/app/inbox"],
    ["unread", data.unread_conversations, CircleAlert, "/app/inbox"],
    ["unassigned", data.unassigned_conversations, UserRoundX, "/app/inbox"],
    ["contacts", data.active_contacts, ContactRound, "/app/contacts"],
    ["openLeads", data.open_leads, CheckCircle2, "/app/leads"],
    ["overdue", data.overdue_follow_ups, ListTodo, "/app/tasks"],
  ] as const;
  return (
    <>
      <PageHeading
        title={t("overview.title")}
        description={t("overview.description")}
        actions={
          <Link className="button primary" href="/app/inbox">
            <Inbox /> {t("overview.openInbox")}
          </Link>
        }
      />
      <section className="welcome-strip crm-welcome">
        <div>
          <span className="eyebrow">{t("overview.workspace")}</span>
          <h2>{workspace.membership?.organization_name}</h2>
          <p>{t("overview.truthfulHint")}</p>
        </div>
        <div className="readiness-block">
          <span>{t("overview.onboarding")}</span>
          <strong>{data.onboarding_completion_percentage}%</strong>
          <Link href="/app/onboarding">{t("overview.reviewSetup")}</Link>
        </div>
      </section>
      <section
        className="metric-grid crm-metrics"
        aria-label={t("overview.metricsLabel")}
      >
        {cards.map(([key, value, Icon, href]) => (
          <Link className="metric-card" key={key} href={href}>
            <div className="metric-icon">
              <Icon aria-hidden="true" />
            </div>
            <div>
              <span>{t(`overview.metrics.${key}`)}</span>
              <strong>{value}</strong>
            </div>
          </Link>
        ))}
      </section>
      <div className="dashboard-grid crm-dashboard">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("overview.pipelineEyebrow")}</span>
              <h2>{t("overview.pipelineTitle")}</h2>
            </div>
            <Link href="/app/leads">{t("overview.viewLeads")}</Link>
          </div>
          {data.leads_by_stage.length ? (
            <ul className="stage-metrics">
              {data.leads_by_stage.map((stage) => (
                <li key={stage.stage_id}>
                  <span
                    className={`stage-token token-${stage.stage__color_token}`}
                  />
                  <strong>{stage.stage__name}</strong>
                  <span>{stage.count}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="compact-empty">{t("overview.noLeads")}</p>
          )}
        </section>
        <section className="panel readiness-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("overview.readinessEyebrow")}</span>
              <h2>{t("overview.readinessTitle")}</h2>
            </div>
          </div>
          <ul className="readiness-list">
            <li>
              <Radio />
              <span>{t("overview.channels")}</span>
              <strong>{data.configured_channels}</strong>
            </li>
            <li>
              <Bot />
              <span>{t("overview.aiContext")}</span>
              <StatusBadge status={data.ai_context_status} />
              <small>v{data.ai_context_version}</small>
            </li>
            <li>
              <CheckCircle2 />
              <span>{t("overview.onboarding")}</span>
              <strong>{data.onboarding_completion_percentage}%</strong>
            </li>
          </ul>
        </section>
        <section className="panel activity-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("overview.activityEyebrow")}</span>
              <h2>{t("overview.activityTitle")}</h2>
            </div>
          </div>
          {activity.data?.results.length ? (
            <ul className="activity-list">
              {activity.data.results.map((item) => (
                <li key={item.id}>
                  <span className="activity-dot" />
                  <div>
                    <strong>{item.summary}</strong>
                    <p>{item.actor_name || t("overview.systemActor")}</p>
                    <time dateTime={item.created_at}>
                      {formatDateTime(item.created_at, locale)}
                    </time>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="compact-empty">{t("overview.noActivity")}</p>
          )}
        </section>
      </div>
    </>
  );
}
