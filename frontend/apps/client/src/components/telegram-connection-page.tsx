"use client";

import type { TelegramBotConnection } from "@workspace/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Circle,
  RefreshCw,
  RotateCw,
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

function date(value: string | null, locale: string) {
  return value
    ? new Intl.DateTimeFormat(locale, {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
}

export function TelegramConnectionPage({
  connectionId,
}: {
  connectionId: string;
}) {
  const t = useTranslations("telegram");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [notice, setNotice] = useState("");
  const [accessDraft, setAccessDraft] = useState<{
    restricted: boolean;
    ids: string;
  } | null>(null);
  const [replacementToken, setReplacementToken] = useState("");
  const editable =
    can(workspace.membership?.role, "manage_channels") &&
    !["suspended", "archived"].includes(
      workspace.membership?.organization_status ?? "",
    );
  const query = useQuery({
    queryKey: ["telegram", organizationId, "connection", connectionId],
    queryFn: () => workspace.api.telegramConnection(connectionId),
  });
  const refresh = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["telegram", organizationId],
    });
  };
  const mutation = useMutation({
    mutationFn: (operation: () => Promise<TelegramBotConnection | unknown>) =>
      operation(),
    onSuccess: async () => {
      setNotice(t("saved"));
      setReplacementToken("");
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
  const health = connection.health;
  const accessRestricted =
    accessDraft?.restricted ?? connection.access_restricted;
  const accessIds =
    accessDraft?.ids ?? connection.permitted_telegram_user_ids.join(", ");
  const saveAccess = () => {
    const ids = accessIds
      .split(",")
      .map((item) => Number(item.trim()))
      .filter(Boolean);
    mutation.mutate(() =>
      workspace.api.updateTelegramAccess(connectionId, {
        access_restricted: accessRestricted,
        permitted_telegram_user_ids: ids,
      }),
    );
  };
  return (
    <>
      <Link className="back-link" href="/app/settings/channels/telegram">
        <ArrowLeft /> {t("back")}
      </Link>
      <PageHeading
        title={`@${connection.bot_username}`}
        description={connection.bot_name}
        actions={<StatusBadge status={connection.status} />}
      />
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      <div className="telegram-detail-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("healthEyebrow")}</span>
              <h2>{t("healthTitle")}</h2>
            </div>
            <ShieldCheck />
          </div>
          <dl className="detail-list">
            <div>
              <dt>{t("type")}</dt>
              <dd>{t(`types.${connection.connection_type}`)}</dd>
            </div>
            <div>
              <dt>{t("token")}</dt>
              <dd>
                {connection.has_encrypted_token
                  ? t("encryptedHealthy")
                  : t("tokenMissing")}
              </dd>
            </div>
            <div>
              <dt>{t("tokenVersion")}</dt>
              <dd>{connection.token_version}</dd>
            </div>
            <div>
              <dt>{t("webhook")}</dt>
              <dd>{health.webhook_status}</dd>
            </div>
            <div>
              <dt>{t("provider")}</dt>
              <dd>
                {health.provider_reachable === false
                  ? t("unreachable")
                  : t("healthy")}
              </dd>
            </div>
            <div>
              <dt>{t("lastHealth")}</dt>
              <dd>{date(connection.last_health_check_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastInbound")}</dt>
              <dd>{date(connection.last_update_at, locale)}</dd>
            </div>
            <div>
              <dt>{t("lastOutbound")}</dt>
              <dd>{date(connection.last_send_at, locale)}</dd>
            </div>
          </dl>
          {editable ? (
            <button
              className="button secondary"
              onClick={() =>
                mutation.mutate(() =>
                  workspace.api.telegramHealth(connectionId, true),
                )
              }
            >
              <RefreshCw /> {t("refreshHealth")}
            </button>
          ) : null}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("automation")}</span>
              <h2>{t("automationTitle")}</h2>
              <p>{t("automationDescription")}</p>
            </div>
            <Send />
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
                disabled={!editable || mutation.isPending}
                onClick={() =>
                  mutation.mutate(() =>
                    workspace.api.updateTelegramConnection(connectionId, {
                      automation_mode: mode,
                    }),
                  )
                }
              >
                <span>
                  {connection.automation_mode === mode ? <Check /> : <Circle />}
                </span>
                <strong>{t(`modes.${mode}`)}</strong>
                <small>{t(`modeDescriptions.${mode}`)}</small>
              </button>
            ))}
          </div>
          <label className="field">
            <span>{t("defaultLanguage")}</span>
            <select
              value={connection.default_language}
              disabled={!editable}
              onChange={(event) =>
                mutation.mutate(() =>
                  workspace.api.updateTelegramConnection(connectionId, {
                    default_language: event.target.value as "ru" | "uz" | "en",
                  }),
                )
              }
            >
              <option value="ru">Русский</option>
              <option value="uz">O‘zbekcha</option>
              <option value="en">English</option>
            </select>
          </label>
          <label className="field">
            <span>{t("privacyUrl")}</span>
            <input
              type="url"
              defaultValue={connection.privacy_url}
              disabled={!editable}
              onBlur={(event) =>
                mutation.mutate(() =>
                  workspace.api.updateTelegramConnection(connectionId, {
                    privacy_url: event.target.value,
                  }),
                )
              }
            />
          </label>
        </section>
      </div>
      {connection.connection_type === "managed" ? (
        <section className="panel telegram-access-panel">
          <div className="panel-heading">
            <div>
              <span className="eyebrow">{t("managedAccess")}</span>
              <h2>{t("managedAccessTitle")}</h2>
              <p>{t("managedAccessDescription")}</p>
            </div>
          </div>
          <label className="toggle-row">
            <span>
              <strong>{t("restrictAccess")}</strong>
              <small>{t("restrictAccessHint")}</small>
            </span>
            <input
              type="checkbox"
              checked={accessRestricted}
              disabled={!editable}
              onChange={(event) =>
                setAccessDraft({
                  restricted: event.target.checked,
                  ids: accessIds,
                })
              }
            />
          </label>
          <label className="field">
            <span>{t("permittedIds")}</span>
            <input
              inputMode="numeric"
              value={accessIds}
              placeholder={connection.permitted_telegram_user_ids.join(", ")}
              onChange={(event) =>
                setAccessDraft({
                  restricted: accessRestricted,
                  ids: event.target.value,
                })
              }
              disabled={!editable}
            />
          </label>
          {editable ? (
            <button className="button secondary" onClick={saveAccess}>
              {t("saveAccess")}
            </button>
          ) : null}
        </section>
      ) : null}
      {editable ? (
        <section className="panel danger-zone">
          <div>
            <h2>{t("connectionActions")}</h2>
            <p>{t("connectionActionsDescription")}</p>
          </div>
          <label className="field">
            <span>
              {connection.connection_type === "managed"
                ? t("managedRotation")
                : t("replacementToken")}
            </span>
            {connection.connection_type === "existing" ? (
              <input
                type="password"
                autoComplete="off"
                value={replacementToken}
                onChange={(event) => setReplacementToken(event.target.value)}
              />
            ) : (
              <small>{t("managedRotationHint")}</small>
            )}
          </label>
          <div className="form-actions">
            <button
              className="button secondary"
              disabled={
                mutation.isPending ||
                (connection.connection_type === "existing" && !replacementToken)
              }
              onClick={() => {
                if (window.confirm(t("rotateConfirm")))
                  mutation.mutate(() =>
                    workspace.api.rotateTelegramToken(
                      connectionId,
                      replacementToken,
                    ),
                  );
              }}
            >
              <RotateCw /> {t("rotateToken")}
            </button>
            {connection.status === "paused" ||
            connection.status === "disconnected" ? (
              <button
                className="button secondary"
                onClick={() =>
                  mutation.mutate(() =>
                    workspace.api.telegramAction(connectionId, "reconnect"),
                  )
                }
              >
                <RefreshCw /> {t("reconnect")}
              </button>
            ) : (
              <button
                className="button secondary"
                onClick={() =>
                  mutation.mutate(() =>
                    workspace.api.telegramAction(connectionId, "pause"),
                  )
                }
              >
                {t("pause")}
              </button>
            )}
            <button
              className="button secondary danger"
              onClick={() => {
                if (window.confirm(t("disconnectConfirm")))
                  mutation.mutate(() =>
                    workspace.api.telegramAction(connectionId, "disconnect"),
                  );
              }}
            >
              <Unplug /> {t("disconnect")}
            </button>
          </div>
        </section>
      ) : null}
    </>
  );
}
