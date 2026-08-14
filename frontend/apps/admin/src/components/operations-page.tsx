"use client";

import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Filter,
  LockKeyhole,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";
import { Link } from "@/i18n/navigation";
import { internalApi, InternalApiError, type JsonRecord } from "@/lib/api";
import {
  collectionFromPayload,
  displayValue,
  metricValue,
  roleCanManage,
  safeEntries,
  sectionEndpoint,
  type Section,
} from "@/lib/presentation";
import { useInternalSession } from "./admin-shell";

export function OperationsPage({ section }: { section: Section }) {
  const t = useTranslations();
  const me = useInternalSession();
  const [payload, setPayload] = useState<unknown>(null);
  const [error, setError] = useState<InternalApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [dialog, setDialog] = useState<"ai" | "incident" | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setPayload(await internalApi.get(sectionEndpoint[section]));
    } catch (caught) {
      setError(
        caught instanceof InternalApiError
          ? caught
          : new InternalApiError(
              "Unable to load platform data",
              500,
              "load_failed",
            ),
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    let active = true;
    void internalApi
      .get(sectionEndpoint[section])
      .then((value) => {
        if (active) setPayload(value);
      })
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof InternalApiError
              ? caught
              : new InternalApiError(
                  "Unable to load platform data",
                  500,
                  "load_failed",
                ),
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [section]);

  const rows = useMemo(
    () =>
      collectionFromPayload(payload).filter(
        (item) =>
          !query ||
          JSON.stringify(item).toLowerCase().includes(query.toLowerCase()),
      ),
    [payload, query],
  );
  const metrics =
    payload && typeof payload === "object" && !Array.isArray(payload)
      ? safeEntries(payload)
      : [];
  const manageable = Boolean(me && roleCanManage(me.role, section));

  return (
    <section className="operations-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">{t("common.internal")}</p>
          <h1>{t(`pages.${section}`)}</h1>
          <p>Live stored data only · sensitive values are never serialized.</p>
        </div>
        <button
          className="secondary-button compact"
          onClick={() => void load()}
        >
          <RefreshCw />
          {t("common.retry")}
        </button>
      </header>
      <div className="assurance-strip">
        <ShieldCheck />
        <span>{t("common.redacted")}</span>
        <i />
        Separate internal authorization
        <i />
        Every privileged action requires a reason
      </div>
      {manageable && section === "overview" ? (
        <QuickActions
          onControl={() => setDialog("ai")}
          onIncident={() => setDialog("incident")}
        />
      ) : null}
      {error ? (
        <ErrorPanel error={error} retry={() => void load()} />
      ) : loading ? (
        <div className="panel loading-panel">
          <span className="spinner" />
          {t("common.loading")}
        </div>
      ) : (
        <>
          {metrics.length ? (
            <div className="metric-grid">
              {metrics.slice(0, 12).map(([key, value]) => (
                <Metric key={key} label={key} value={value} />
              ))}
            </div>
          ) : null}
          {rows.length || section !== "overview" ? (
            <section className="panel data-panel">
              <div className="panel-title">
                <div>
                  <h2>{t(`pages.${section}`)}</h2>
                  <span>{rows.length} stored records</span>
                </div>
                <label className="search">
                  <Search />
                  <span className="sr-only">Search</span>
                  <input
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Filter this view"
                  />
                  <Filter />
                </label>
              </div>
              {rows.length ? (
                <div className="records">
                  {rows.map((row, index) => (
                    <RecordCard
                      key={String((row as JsonRecord)?.id ?? index)}
                      row={row}
                      section={section}
                    />
                  ))}
                </div>
              ) : (
                <div className="empty">
                  <CheckCircle2 />
                  <p>{t("common.noData")}</p>
                </div>
              )}
            </section>
          ) : null}
        </>
      )}
      {dialog === "ai" ? (
        <ReasonDialog
          title={t("actions.disableAi")}
          description="Blocks new AI work platform-wide. Queued work is superseded safely."
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            await internalApi.mutate(
              "/controls/",
              { action: "activate", kind: "global_ai", reason },
              "PATCH",
            );
            setDialog(null);
            await load();
          }}
        />
      ) : null}
      {dialog === "incident" ? (
        <ReasonDialog
          title={t("actions.createIncident")}
          description="Creates a safe internal incident without customer content."
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            await internalApi.mutate("/incidents/", {
              severity: "medium",
              title: "Operational review",
              safe_summary:
                "Internal review created from the platform overview.",
              reason,
            });
            setDialog(null);
            await load();
          }}
        />
      ) : null}
    </section>
  );
}

function QuickActions({
  onControl,
  onIncident,
}: {
  onControl: () => void;
  onIncident: () => void;
}) {
  return (
    <section className="quick-actions" aria-label="Emergency actions">
      <button onClick={onControl}>
        <LockKeyhole />
        <span>
          <strong>Global AI safety control</strong>
          <small>Recent MFA and reason required</small>
        </span>
        <ArrowRight />
      </button>
      <button onClick={onIncident}>
        <AlertTriangle />
        <span>
          <strong>Create operational incident</strong>
          <small>Plain-text safe summary only</small>
        </span>
        <ArrowRight />
      </button>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  if (value && typeof value === "object")
    return (
      <article className="metric-card">
        <span>{label.replaceAll("_", " ")}</span>
        <strong>{metricValue(value)}</strong>
        <small>
          {safeEntries(value)
            .slice(0, 3)
            .map(([key, item]) => `${key}: ${displayValue(item)}`)
            .join(" · ")}
        </small>
      </article>
    );
  return (
    <article className="metric-card">
      <span>{label.replaceAll("_", " ")}</span>
      <strong>{displayValue(value)}</strong>
      <small>Stored platform value</small>
    </article>
  );
}

function RecordCard({ row, section }: { row: unknown; section: Section }) {
  const record = (
    row && typeof row === "object" ? row : { value: row }
  ) as JsonRecord;
  const identifier = String(record.id ?? record.organization ?? "");
  return (
    <article className="record-card">
      <div className="record-status">
        <i className={`state-${String(record.status ?? "active")}`} />
        <span>{displayValue(record.status ?? record.type ?? "record")}</span>
      </div>
      <dl>
        {safeEntries(record)
          .slice(0, 8)
          .map(([key, value]) => (
            <div key={key}>
              <dt>{key.replaceAll("_", " ")}</dt>
              <dd>{displayValue(value)}</dd>
            </div>
          ))}
      </dl>
      {section === "organizations" && identifier ? (
        <Link className="text-link" href={`/app/organizations/${identifier}`}>
          Inspect safely <ArrowRight />
        </Link>
      ) : null}
    </article>
  );
}

function ErrorPanel({
  error,
  retry,
}: {
  error: InternalApiError;
  retry: () => void;
}) {
  const t = useTranslations();
  return (
    <section className="panel error-panel" role="alert">
      <AlertTriangle />
      <div>
        <h2>{error.message}</h2>
        <p>
          {error.requestId
            ? t("common.requestId", { id: error.requestId })
            : error.code}
        </p>
      </div>
      <button onClick={retry}>{t("common.retry")}</button>
    </section>
  );
}

export function ReasonDialog({
  title,
  description,
  onClose,
  onConfirm,
}: {
  title: string;
  description: string;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const t = useTranslations();
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function confirm() {
    setBusy(true);
    setError("");
    try {
      await onConfirm(reason);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="dialog-backdrop" role="presentation">
      <section
        className="reason-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="dialog-title"
      >
        <div className="dialog-icon">
          <AlertTriangle />
        </div>
        <h2 id="dialog-title">{title}</h2>
        <p>{description}</p>
        <label>
          <span>{t("common.reason")}</span>
          <textarea
            autoFocus
            minLength={8}
            maxLength={1000}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        {error ? (
          <p className="form-error" role="alert">
            {error}
          </p>
        ) : null}
        <div>
          <button className="secondary-button" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button
            className="danger-button"
            disabled={busy || reason.trim().length < 8}
            onClick={() => void confirm()}
          >
            {t("common.confirm")}
          </button>
        </div>
      </section>
    </div>
  );
}
