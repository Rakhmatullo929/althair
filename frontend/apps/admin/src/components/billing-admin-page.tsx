"use client";

import {
  AlertTriangle,
  CheckCircle2,
  CreditCard,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";
import { Link } from "@/i18n/navigation";
import { internalApi, InternalApiError, type JsonRecord } from "@/lib/api";
import {
  billingActionPath,
  canManageBilling,
  canReconcileBilling,
  validReviewedReason,
} from "@/lib/billing";
import { useInternalSession } from "./admin-shell";

type Section = "plans" | "subscriptions" | "invoices" | "usage";
type Action = { kind: string; id?: string; organization?: string };

const endpoints: Record<Section, string> = {
  plans: "/billing/plans/",
  subscriptions: "/billing/subscriptions/",
  invoices: "/billing/invoices/",
  usage: "/billing/usage/",
};

function rowsFrom(payload: unknown): JsonRecord[] {
  if (Array.isArray(payload)) return payload as JsonRecord[];
  if (payload && typeof payload === "object") {
    const results = (payload as JsonRecord).results;
    return Array.isArray(results) ? (results as JsonRecord[]) : [];
  }
  return [];
}

export function BillingAdminPage({ section }: { section: Section }) {
  const t = useTranslations("billing");
  const me = useInternalSession();
  const [payload, setPayload] = useState<unknown>(null);
  const [events, setEvents] = useState<JsonRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [createPlan, setCreatePlan] = useState(false);

  async function load() {
    setLoading(true);
    setError("");
    try {
      const next = await internalApi.get(endpoints[section]);
      setPayload(next);
      if (section === "usage") {
        const provider = await internalApi.get<{ results: JsonRecord[] }>(
          "/billing/provider-events/",
        );
        setEvents(provider.results);
      }
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Billing data unavailable",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let active = true;
    void internalApi
      .get(endpoints[section])
      .then((next) => active && setPayload(next))
      .catch(
        (caught: unknown) =>
          active &&
          setError(
            caught instanceof Error
              ? caught.message
              : "Billing data unavailable",
          ),
      )
      .finally(() => active && setLoading(false));
    if (section === "usage")
      void internalApi
        .get<{ results: JsonRecord[] }>("/billing/provider-events/")
        .then((next) => active && setEvents(next.results));
    return () => {
      active = false;
    };
  }, [section]);

  const rows = useMemo(
    () =>
      rowsFrom(payload).filter(
        (row) =>
          !query ||
          JSON.stringify(row).toLowerCase().includes(query.toLowerCase()),
      ),
    [payload, query],
  );
  const canManage = canManageBilling(me?.role ?? "");
  const canReconcile = canReconcileBilling(me?.role ?? "");

  return (
    <section className="internal-billing-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">{t("internal")}</p>
          <h1>{t(`sections.${section}`)}</h1>
          <p>{t("description")}</p>
        </div>
        <div className="page-heading-actions">
          {section === "plans" && canManage ? (
            <button
              className="primary-button"
              onClick={() => setCreatePlan(true)}
            >
              <Plus /> {t("createPlan")}
            </button>
          ) : null}
          <button
            className="secondary-button compact"
            onClick={() => void load()}
          >
            <RefreshCw /> {t("refresh")}
          </button>
        </div>
      </header>
      <nav className="internal-billing-tabs" aria-label={t("navigation")}>
        {(["plans", "subscriptions", "invoices", "usage"] as const).map(
          (key) => (
            <Link
              key={key}
              href={`/app/billing/${key}`}
              aria-current={key === section ? "page" : undefined}
            >
              {t(`sections.${key}`)}
            </Link>
          ),
        )}
      </nav>
      <div className="assurance-strip">
        <ShieldCheck /> {t("assurance")} <i />{" "}
        {me?.mfa_fresh ? t("mfaFresh") : t("mfaRequired")}
      </div>
      {notice ? (
        <div className="admin-notice" role="status">
          {notice}
        </div>
      ) : null}
      {error ? (
        <div className="error-panel" role="alert">
          <AlertTriangle />
          <p>{error}</p>
        </div>
      ) : null}
      <section className="panel data-panel">
        <div className="panel-title">
          <div>
            <h2>{t(`sections.${section}`)}</h2>
            <span>{t("storedRecords", { count: rows.length })}</span>
          </div>
          <label className="search">
            <Search />
            <span className="sr-only">{t("search")}</span>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("search")}
            />
          </label>
        </div>
        {loading ? (
          <div className="loading-panel">
            <span className="spinner" />
            {t("loading")}
          </div>
        ) : rows.length ? (
          <div className="billing-admin-records">
            {rows.map((row, index) => (
              <BillingRecord
                key={String(row.id ?? row.meter_key ?? index)}
                section={section}
                row={row}
                canManage={canManage}
                canReconcile={canReconcile}
                act={setAction}
              />
            ))}
          </div>
        ) : (
          <div className="empty">
            <CheckCircle2 />
            <p>{t("noData")}</p>
          </div>
        )}
      </section>
      {section === "usage" ? (
        <section className="panel provider-event-panel">
          <div className="panel-title">
            <div>
              <h2>{t("providerEvents")}</h2>
              <span>{t("providerEventsHelp")}</span>
            </div>
          </div>
          {events.length ? (
            <div className="billing-admin-records">
              {events.map((event) => (
                <BillingRecord
                  key={String(event.id)}
                  section="usage"
                  row={event}
                  canManage={false}
                  canReconcile={false}
                  act={setAction}
                />
              ))}
            </div>
          ) : (
            <p>{t("noProviderEvents")}</p>
          )}
        </section>
      ) : null}
      {createPlan ? (
        <CreatePlanDialog
          close={() => setCreatePlan(false)}
          complete={async () => {
            setCreatePlan(false);
            setNotice(t("planCreated"));
            await load();
          }}
        />
      ) : null}
      {action ? (
        <BillingActionDialog
          action={action}
          close={() => setAction(null)}
          complete={async () => {
            setAction(null);
            setNotice(t("actionComplete"));
            await load();
          }}
        />
      ) : null}
    </section>
  );
}

function BillingRecord({
  section,
  row,
  canManage,
  canReconcile,
  act,
}: {
  section: Section;
  row: JsonRecord;
  canManage: boolean;
  canReconcile: boolean;
  act: (value: Action) => void;
}) {
  const t = useTranslations("billing");
  const id = String(row.id ?? "");
  const status = String(row.status ?? "active");
  const organization = String(row.organization ?? "");
  const visible = Object.entries(row)
    .filter(
      ([key]) =>
        ![
          "feature_values",
          "lines",
          "payment_attempts",
          "prices",
          "plan",
        ].includes(key),
    )
    .slice(0, 9);
  return (
    <article className="billing-admin-card">
      <div className="billing-admin-state">
        <i className={`state-${status}`} />
        <strong>
          {String(
            row.display_name ??
              row.invoice_number ??
              row.meter_key ??
              row.id ??
              t("record"),
          )}
        </strong>
        <span>{status.replaceAll("_", " ")}</span>
      </div>
      <dl>
        {visible.map(([key, value]) => (
          <div key={key}>
            <dt>{key.replaceAll("_", " ")}</dt>
            <dd>
              {typeof value === "object"
                ? JSON.stringify(value)
                : String(value ?? "—")}
            </dd>
          </div>
        ))}
      </dl>
      <div className="billing-admin-actions">
        {section === "plans" && canManage && status === "draft" ? (
          <button onClick={() => act({ kind: "publish", id })}>
            {t("publish")}
          </button>
        ) : null}
        {section === "subscriptions" && canManage ? (
          <>
            <button onClick={() => act({ kind: "grant", id, organization })}>
              {t("grantManual")}
            </button>
            <button onClick={() => act({ kind: "grace", id })}>
              {t("extendGrace")}
            </button>
          </>
        ) : null}
        {section === "invoices" && canManage && status === "draft" ? (
          <button onClick={() => act({ kind: "issue", id })}>
            {t("issue")}
          </button>
        ) : null}
        {section === "invoices" &&
        canManage &&
        ["draft", "open"].includes(status) ? (
          <button onClick={() => act({ kind: "void", id })}>{t("void")}</button>
        ) : null}
        {section === "invoices" && canManage && status === "open" ? (
          <button onClick={() => act({ kind: "mark-paid", id })}>
            {t("markPaid")}
          </button>
        ) : null}
        {section === "usage" && canReconcile && organization ? (
          <button onClick={() => act({ kind: "reconcile", organization })}>
            {t("reconcile")}
          </button>
        ) : null}
      </div>
    </article>
  );
}

function CreatePlanDialog({
  close,
  complete,
}: {
  close: () => void;
  complete: () => Promise<void>;
}) {
  const t = useTranslations("billing");
  const [form, setForm] = useState({
    key: "",
    display_name: "",
    description: "",
    audience: "self_serve",
    currency: "UZS",
    billing_interval: "month",
    amount_minor: "0",
    reason: "",
  });
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const submit = async () => {
    setPending(true);
    setError("");
    try {
      await internalApi.mutate("/billing/plans/", {
        ...form,
        amount_minor: Number(form.amount_minor),
        feature_values: { crm: true, billing_access: true, data_export: true },
        included_usage: {},
        overage_rates: {},
      });
      await complete();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : t("actionFailed"));
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="modal-backdrop">
      <div
        className="admin-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-plan-title"
      >
        <h2 id="create-plan-title">{t("createPlan")}</h2>
        <p>{t("draftPlanHelp")}</p>
        <div className="admin-dialog-grid">
          {(
            [
              "key",
              "display_name",
              "description",
              "currency",
              "amount_minor",
              "reason",
            ] as const
          ).map((key) => (
            <label key={key}>
              <span>{t(`fields.${key}`)}</span>
              <input
                value={form[key]}
                onChange={(event) =>
                  setForm({ ...form, [key]: event.target.value })
                }
              />
            </label>
          ))}
          <label>
            <span>{t("fields.audience")}</span>
            <select
              value={form.audience}
              onChange={(event) =>
                setForm({ ...form, audience: event.target.value })
              }
            >
              <option value="self_serve">self serve</option>
              <option value="sales_assisted">sales assisted</option>
              <option value="internal">internal</option>
            </select>
          </label>
        </div>
        {error ? <p className="dialog-error">{error}</p> : null}
        <div className="dialog-actions">
          <button onClick={close}>{t("close")}</button>
          <button
            className="primary-button"
            disabled={
              pending ||
              form.reason.length < 8 ||
              !form.key ||
              !form.display_name
            }
            onClick={() => void submit()}
          >
            {t("createDraft")}
          </button>
        </div>
      </div>
    </div>
  );
}

function BillingActionDialog({
  action,
  close,
  complete,
}: {
  action: Action;
  close: () => void;
  complete: () => Promise<void>;
}) {
  const t = useTranslations("billing");
  const [reason, setReason] = useState("");
  const [days, setDays] = useState("7");
  const [priceId, setPriceId] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const submit = async () => {
    setPending(true);
    setError("");
    try {
      const path = billingActionPath(action.kind as never, action.id);
      if (action.kind === "grace")
        await internalApi.mutate(path, { reason, days: Number(days) });
      else if (action.kind === "grant")
        await internalApi.mutate(path, {
          reason,
          organization_id: action.organization,
          price_id: priceId,
          period_days: Number(days),
        });
      else if (action.kind === "reconcile")
        await internalApi.mutate(path, {
          reason,
          organization_id: action.organization,
        });
      else await internalApi.mutate(path, { reason });
      await complete();
    } catch (caught) {
      setError(
        caught instanceof InternalApiError ? caught.message : t("actionFailed"),
      );
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="modal-backdrop">
      <div
        className="admin-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="billing-action-title"
      >
        <CreditCard />
        <h2 id="billing-action-title">{t(`actions.${action.kind}`)}</h2>
        <p>{t("mfaActionHelp")}</p>
        {action.kind === "grant" ? (
          <label>
            <span>{t("fields.price_id")}</span>
            <input
              value={priceId}
              onChange={(event) => setPriceId(event.target.value)}
            />
          </label>
        ) : null}
        {["grant", "grace"].includes(action.kind) ? (
          <label>
            <span>{t("fields.days")}</span>
            <input
              type="number"
              min="1"
              max={action.kind === "grace" ? 90 : 3660}
              value={days}
              onChange={(event) => setDays(event.target.value)}
            />
          </label>
        ) : null}
        <label>
          <span>{t("fields.reason")}</span>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={1000}
          />
        </label>
        {error ? <p className="dialog-error">{error}</p> : null}
        <div className="dialog-actions">
          <button onClick={close}>{t("close")}</button>
          <button
            className="primary-button"
            disabled={
              pending ||
              !validReviewedReason(reason) ||
              (action.kind === "grant" && !priceId)
            }
            onClick={() => void submit()}
          >
            {t("confirm")}
          </button>
        </div>
      </div>
    </div>
  );
}
