"use client";

import type {
  TelegramManagedBotRequest,
  TelegramUserLink,
} from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ExternalLink,
  Link2,
  LockKeyhole,
  Plus,
  Send,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import { can } from "@/lib/permissions";
import { useWorkspace } from "./workspace-provider";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";

export function TelegramConnectionsPage() {
  const t = useTranslations("telegram");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const [notice, setNotice] = useState("");
  const [freshLink, setFreshLink] = useState<TelegramUserLink | null>(null);
  const [creation, setCreation] = useState<{
    request: TelegramManagedBotRequest;
    creation_url: string;
  } | null>(null);
  const [botName, setBotName] = useState("Althair Support");
  const [botUsername, setBotUsername] = useState("AlthairSupportBot");
  const [existingToken, setExistingToken] = useState("");

  const readiness = useQuery({
    queryKey: ["telegram", "readiness"],
    queryFn: () => workspace.api.telegramReadiness(),
  });
  const identity = useQuery({
    queryKey: ["telegram", organizationId, "identity"],
    queryFn: () => workspace.api.telegramIdentity(),
  });
  const connections = useQuery({
    queryKey: ["telegram", organizationId, "connections"],
    queryFn: () => workspace.api.telegramConnections(),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["telegram", organizationId],
    });
  };
  const action = useMutation({
    mutationFn: (operation: () => Promise<unknown>) => operation(),
    onSuccess: async () => {
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const startIdentity = () =>
    action.mutate(async () => {
      const link = await workspace.api.createTelegramIdentityLink();
      setFreshLink(link);
      return link;
    });
  const simulateIdentity = () =>
    action.mutate(async () => {
      const url = new URL(freshLink!.telegram_url!);
      const startParameter = url.searchParams.get("start") ?? "";
      const result = await workspace.api.telegramTestManagerEvent({
        event_type: "identity_link",
        start_parameter: startParameter,
      });
      setFreshLink(null);
      return result;
    });
  const createManaged = () =>
    action.mutate(async () => {
      const result = await workspace.api.createTelegramManagedRequest({
        suggested_name: botName,
        suggested_username: botUsername,
      });
      setCreation(result);
      return result;
    });
  const simulateCreation = () =>
    action.mutate(async () => {
      const result = await workspace.api.telegramTestManagerEvent({
        event_type: "managed_bot",
        request_id: creation!.request.id,
      });
      setCreation(null);
      return result;
    });
  const connectExisting = () =>
    action.mutate(async () => {
      const result =
        await workspace.api.connectExistingTelegramBot(existingToken);
      setExistingToken("");
      return result;
    });

  if (readiness.isLoading || identity.isLoading || connections.isLoading)
    return <PageSkeleton />;
  const error = readiness.error ?? identity.error ?? connections.error;
  if (error)
    return (
      <ErrorState
        title={t("errorTitle")}
        description={(error as Error).message}
        onRetry={() => void refresh()}
      />
    );
  const linked = identity.data?.status === "linked";
  const fake = readiness.data?.fake_provider;
  return (
    <>
      <PageHeading title={t("title")} description={t("description")} />
      <div className="info-banner telegram-policy-banner">
        <LockKeyhole aria-hidden="true" />
        <div>
          <strong>{t("secureTitle")}</strong>
          <p>{t("secureDescription")}</p>
        </div>
        <StatusBadge
          status={readiness.data?.ready ? "ready" : "configuration_incomplete"}
        />
      </div>
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {!editable ? <div className="readonly-note">{t("readOnly")}</div> : null}

      {connections.data!.results.length ? (
        <section className="channel-grid" aria-label={t("connections")}>
          {connections.data!.results.map((connection) => (
            <article className="channel-card" key={connection.id}>
              <div className="channel-card-heading">
                <div className="channel-icon telegram-gradient">
                  <Send aria-hidden="true" />
                </div>
                <div>
                  <h2>@{connection.bot_username}</h2>
                  <StatusBadge status={connection.status} />
                </div>
              </div>
              <p>{connection.bot_name}</p>
              <dl>
                <div>
                  <dt>{t("type")}</dt>
                  <dd>{t(`types.${connection.connection_type}`)}</dd>
                </div>
                <div>
                  <dt>{t("webhook")}</dt>
                  <dd>{connection.webhook_status}</dd>
                </div>
                <div>
                  <dt>{t("automation")}</dt>
                  <dd>{t(`modes.${connection.automation_mode}`)}</dd>
                </div>
              </dl>
              <Link
                className="button secondary"
                href={`/app/settings/channels/telegram/${connection.id}`}
              >
                {t("manage")} <ExternalLink aria-hidden="true" />
              </Link>
            </article>
          ))}
        </section>
      ) : (
        <EmptyState
          icon={<Bot />}
          title={t("emptyTitle")}
          description={t("emptyDescription")}
        />
      )}

      {connections.data!.results.length === 0 && editable ? (
        <div className="telegram-setup-grid">
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">1</span>
                <h2>{t("linkTitle")}</h2>
                <p>{t("linkDescription")}</p>
              </div>
              <Link2 />
            </div>
            <StatusBadge status={identity.data?.status ?? "not_linked"} />
            {identity.data?.telegram_username ? (
              <p>
                @{identity.data.telegram_username} ·{" "}
                {identity.data.telegram_user_id}
              </p>
            ) : null}
            {freshLink?.telegram_url ? (
              <a
                className="button primary"
                href={freshLink.telegram_url}
                target="_blank"
                rel="noreferrer"
              >
                {t("openManager")} <ExternalLink />
              </a>
            ) : (
              <button
                className="button secondary"
                onClick={startIdentity}
                disabled={action.isPending}
              >
                {t("createLink")}
              </button>
            )}
            {freshLink?.telegram_url && fake ? (
              <button
                className="button secondary"
                onClick={simulateIdentity}
                disabled={action.isPending}
              >
                {t("simulateLink")}
              </button>
            ) : null}
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span className="eyebrow">2</span>
                <h2>{t("managedTitle")}</h2>
                <p>{t("managedDescription")}</p>
              </div>
              <Plus />
            </div>
            <label className="field">
              <span>{t("botName")}</span>
              <input
                value={botName}
                onChange={(event) => setBotName(event.target.value)}
                maxLength={64}
              />
            </label>
            <label className="field">
              <span>{t("botUsername")}</span>
              <input
                value={botUsername}
                onChange={(event) => setBotUsername(event.target.value)}
                maxLength={32}
              />
            </label>
            {!creation ? (
              <button
                className="button primary"
                onClick={createManaged}
                disabled={!linked || action.isPending}
              >
                {t("createManaged")}
              </button>
            ) : (
              <>
                <a
                  className="button primary"
                  href={creation.creation_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {t("confirmTelegram")} <ExternalLink />
                </a>
                {fake ? (
                  <button
                    className="button secondary"
                    onClick={simulateCreation}
                  >
                    {t("simulateCreation")}
                  </button>
                ) : null}
              </>
            )}
          </section>
          <details className="panel telegram-existing-panel">
            <summary>{t("existingTitle")}</summary>
            <p>{t("existingDescription")}</p>
            <label className="field">
              <span>{t("writeOnlyToken")}</span>
              <input
                type="password"
                autoComplete="off"
                value={existingToken}
                onChange={(event) => setExistingToken(event.target.value)}
              />
            </label>
            <button
              className="button secondary"
              onClick={connectExisting}
              disabled={!existingToken || action.isPending}
            >
              {t("connectExisting")}
            </button>
          </details>
        </div>
      ) : null}
    </>
  );
}
