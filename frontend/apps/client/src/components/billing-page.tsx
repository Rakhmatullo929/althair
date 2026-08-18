"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { BillingPlan, BillingPrice } from "@workspace/api-client";
import {
  AlertTriangle,
  ArrowDownLeft,
  ArrowRight,
  ArrowUpRight,
  CalendarClock,
  Check,
  CreditCard,
  Download,
  FileText,
  Gauge,
  ReceiptText,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  WalletCards,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { Link } from "@/i18n/navigation";
import {
  billingBanner,
  canManageBilling,
  enabledEntitlements,
  formatMinorMoney,
  formatUsageQuantity,
  usagePercent,
} from "@/lib/billing";
import { ErrorState, PageHeading, PageSkeleton } from "./ui";
import { useWorkspace } from "./workspace-provider";

type BillingView =
  | "overview"
  | "plans"
  | "usage"
  | "invoices"
  | "invoice"
  | "wallet";

function mutationKey(prefix: string) {
  return `${prefix}:${crypto.randomUUID()}`;
}

export function BillingPage({
  view,
  invoiceId,
}: {
  view: BillingView;
  invoiceId?: string;
}) {
  const t = useTranslations("billing");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const [notice, setNotice] = useState("");
  const [selectedPrice, setSelectedPrice] = useState<BillingPrice | null>(null);
  const [profile, setProfile] = useState<{
    legal_name: string;
    billing_email: string;
    country_code: string;
    tax_id: string;
  } | null>(null);
  const manageable = canManageBilling(workspace.membership?.role);
  const queryPrefix = ["billing", organizationId];
  const subscription = useQuery({
    queryKey: [...queryPrefix, "subscription"],
    queryFn: () => workspace.api.billingSubscription(),
  });
  const account = useQuery({
    queryKey: [...queryPrefix, "account"],
    queryFn: () => workspace.api.billingAccount(),
    enabled: view === "overview",
  });
  const entitlements = useQuery({
    queryKey: [...queryPrefix, "entitlements"],
    queryFn: () => workspace.api.billingEntitlements(),
    enabled: view === "overview" || view === "plans",
  });
  const plans = useQuery({
    queryKey: [...queryPrefix, "plans"],
    queryFn: () => workspace.api.billingPlans(),
    enabled: view === "plans",
  });
  const usage = useQuery({
    queryKey: [...queryPrefix, "usage"],
    queryFn: () => workspace.api.billingUsage(),
    enabled: view === "usage" || view === "overview",
  });
  const invoices = useQuery({
    queryKey: [...queryPrefix, "invoices"],
    queryFn: () => workspace.api.billingInvoices(),
    enabled: view === "invoices" || view === "overview",
  });
  const invoice = useQuery({
    queryKey: [...queryPrefix, "invoice", invoiceId],
    queryFn: () => workspace.api.billingInvoice(invoiceId!),
    enabled: view === "invoice" && Boolean(invoiceId),
  });
  const wallet = useQuery({
    queryKey: [...queryPrefix, "wallet"],
    queryFn: () => workspace.api.billingWallet(),
    enabled: view === "wallet" || view === "overview",
  });
  const walletTransactions = useQuery({
    queryKey: [...queryPrefix, "wallet-transactions"],
    queryFn: () => workspace.api.billingWalletTransactions(),
    enabled: view === "wallet",
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: queryPrefix });
  };
  const saveProfile = useMutation({
    mutationFn: () =>
      workspace.api.updateBillingAccount(
        profile ?? {
          legal_name: account.data?.legal_name ?? "",
          billing_email: account.data?.billing_email ?? "",
          country_code: account.data?.country_code ?? "",
          tax_id: account.data?.tax_id ?? "",
        },
      ),
    onSuccess: async () => {
      setNotice(t("saved"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const cancel = useMutation({
    mutationFn: () =>
      workspace.api.cancelBillingSubscription(
        mutationKey("cancel-subscription"),
      ),
    onSuccess: async () => {
      setNotice(t("cancelScheduled"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const resume = useMutation({
    mutationFn: () =>
      workspace.api.resumeBillingSubscription(
        mutationKey("resume-subscription"),
      ),
    onSuccess: async () => {
      setNotice(t("resumed"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });
  const preview = useMutation({
    mutationFn: (price: BillingPrice) =>
      workspace.api.billingChangePreview(price.id),
    onError: (error: Error) => setNotice(error.message),
  });
  const schedule = useMutation({
    mutationFn: (price: BillingPrice) =>
      workspace.api.scheduleBillingChange(
        price.id,
        mutationKey("schedule-plan-change"),
      ),
    onSuccess: async () => {
      setSelectedPrice(null);
      preview.reset();
      setNotice(t("changeScheduled"));
      await refresh();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const relevant = [
    subscription,
    account,
    entitlements,
    plans,
    usage,
    invoices,
    invoice,
    wallet,
    walletTransactions,
  ].filter((query) => query.fetchStatus !== "idle");
  if (relevant.some((query) => query.isLoading)) return <PageSkeleton />;
  const error = relevant.find((query) => query.error)?.error;
  if (error)
    return (
      <ErrorState
        title={t("loadError")}
        description={(error as Error).message}
        onRetry={() => void refresh()}
      />
    );
  if (!subscription.data) return <PageSkeleton />;

  const banner = billingBanner(subscription.data);
  const date = (value: string | null) =>
    value
      ? new Intl.DateTimeFormat(locale, { dateStyle: "medium" }).format(
          new Date(value),
        )
      : t("notAvailable");
  const money = (amount: number, currency = subscription.data.price.currency) =>
    formatMinorMoney(amount, currency, locale);

  return (
    <section className="billing-page">
      <PageHeading title={t(`views.${view}`)} description={t("description")} />
      <nav className="billing-tabs" aria-label={t("navigation")}>
        {(["overview", "plans", "usage", "invoices", "wallet"] as const).map(
          (key) => (
            <Link
              key={key}
              href={key === "overview" ? "/app/billing" : `/app/billing/${key}`}
              aria-current={
                view === key || (view === "invoice" && key === "invoices")
                  ? "page"
                  : undefined
              }
            >
              {t(`tabs.${key}`)}
            </Link>
          ),
        )}
      </nav>
      {banner ? (
        <div className={`billing-lifecycle billing-${banner}`} role="status">
          <AlertTriangle aria-hidden="true" />
          <div>
            <strong>{t(`banners.${banner}.title`)}</strong>
            <p>{t(`banners.${banner}.description`)}</p>
          </div>
        </div>
      ) : null}
      {notice ? (
        <div className="crm-notice" role="status">
          {notice}
        </div>
      ) : null}
      {view === "overview" && account.data ? (
        <BillingOverview
          subscription={subscription.data}
          account={account.data}
          entitlements={entitlements.data?.results ?? []}
          usageCount={usage.data?.results.length ?? 0}
          invoiceCount={invoices.data?.count ?? 0}
          wallet={wallet.data?.wallet}
          manageable={manageable}
          profile={
            profile ?? {
              legal_name: account.data.legal_name,
              billing_email: account.data.billing_email,
              country_code: account.data.country_code,
              tax_id: account.data.tax_id,
            }
          }
          setProfile={setProfile}
          saving={saveProfile.isPending}
          save={() => saveProfile.mutate()}
          cancel={() => cancel.mutate()}
          resume={() => resume.mutate()}
          pending={cancel.isPending || resume.isPending}
          date={date}
          money={money}
        />
      ) : null}
      {view === "plans" ? (
        <PlanCatalog
          plans={plans.data?.results ?? []}
          currentPlanId={subscription.data.plan.id}
          manageable={manageable}
          selected={selectedPrice}
          select={(price) => {
            setSelectedPrice(price);
            preview.mutate(price);
          }}
          preview={preview.data}
          schedule={() => selectedPrice && schedule.mutate(selectedPrice)}
          pending={preview.isPending || schedule.isPending}
          date={date}
          money={money}
        />
      ) : null}
      {view === "usage" ? (
        <UsagePanel
          rows={usage.data?.results ?? []}
          estimate={usage.data?.estimate_label ?? ""}
        />
      ) : null}
      {view === "invoices" ? (
        <InvoiceList
          rows={invoices.data?.results ?? []}
          date={date}
          money={money}
        />
      ) : null}
      {view === "invoice" && invoice.data ? (
        <InvoiceDetail row={invoice.data} date={date} money={money} />
      ) : null}
      {view === "wallet" && wallet.data ? (
        <WalletPanel
          overview={wallet.data}
          transactions={walletTransactions.data?.results ?? []}
          date={date}
          money={money}
        />
      ) : null}
    </section>
  );
}

type OverviewProps = {
  subscription: Awaited<
    ReturnType<ReturnType<typeof useWorkspace>["api"]["billingSubscription"]>
  >;
  account: Awaited<
    ReturnType<ReturnType<typeof useWorkspace>["api"]["billingAccount"]>
  >;
  entitlements: Awaited<
    ReturnType<ReturnType<typeof useWorkspace>["api"]["billingEntitlements"]>
  >["results"];
  usageCount: number;
  invoiceCount: number;
  wallet:
    | Awaited<
        ReturnType<ReturnType<typeof useWorkspace>["api"]["billingWallet"]>
      >["wallet"]
    | undefined;
  manageable: boolean;
  profile: {
    legal_name: string;
    billing_email: string;
    country_code: string;
    tax_id: string;
  };
  setProfile: (value: OverviewProps["profile"]) => void;
  saving: boolean;
  save: () => void;
  cancel: () => void;
  resume: () => void;
  pending: boolean;
  date: (value: string | null) => string;
  money: (amount: number, currency?: string) => string;
};

function BillingOverview(props: OverviewProps) {
  const t = useTranslations("billing");
  const enabled = enabledEntitlements(props.entitlements);
  return (
    <>
      <div className="billing-summary-grid">
        <article className="billing-hero-card">
          <div>
            <span>{t("currentPlan")}</span>
            <h2>{props.subscription.plan.display_name}</h2>
          </div>
          <span className="billing-version">
            v{props.subscription.plan.version}
          </span>
          <dl>
            <div>
              <dt>{t("status")}</dt>
              <dd>{t(`statuses.${props.subscription.status}`)}</dd>
            </div>
            <div>
              <dt>{t("periodEnd")}</dt>
              <dd>{props.date(props.subscription.current_period_end)}</dd>
            </div>
            <div>
              <dt>{t("price")}</dt>
              <dd>
                {props.money(props.subscription.price.amount_minor)} /{" "}
                {t(`intervals.${props.subscription.price.billing_interval}`)}
              </dd>
            </div>
          </dl>
          {props.subscription.scheduled_change ? (
            <p className="scheduled-change">
              <CalendarClock />{" "}
              {t("scheduledFor", {
                date: props.date(
                  props.subscription.scheduled_change.effective_at,
                ),
              })}
            </p>
          ) : null}
          {props.manageable ? (
            props.subscription.cancel_at_period_end ? (
              <button
                className="button secondary"
                disabled={props.pending}
                onClick={props.resume}
              >
                <RotateCcw />
                {t("resume")}
              </button>
            ) : (
              <button
                className="button secondary danger"
                disabled={props.pending}
                onClick={props.cancel}
              >
                {t("cancelRenewal")}
              </button>
            )
          ) : (
            <p className="readonly-note">{t("readOnly")}</p>
          )}
        </article>
        <article className="billing-provider-card">
          <WalletCards />
          <div>
            <span>{t("wallet.balance")}</span>
            <h2>
              {props.wallet
                ? props.money(
                    props.wallet.available_balance_minor,
                    props.wallet.currency,
                  )
                : t("notAvailable")}
            </h2>
            <p>{t("wallet.customerReadOnly")}</p>
          </div>
          <Link href="/app/billing/wallet" className="button secondary">
            {t("wallet.viewLedger")}
          </Link>
        </article>
        <Link href="/app/billing/usage" className="billing-stat-card">
          <Gauge />
          <strong>{props.usageCount}</strong>
          <span>{t("usageMeters")}</span>
          <ArrowRight />
        </Link>
        <Link href="/app/billing/invoices" className="billing-stat-card">
          <ReceiptText />
          <strong>{props.invoiceCount}</strong>
          <span>{t("invoiceCount")}</span>
          <ArrowRight />
        </Link>
      </div>
      <section className="panel billing-features">
        <div className="panel-heading">
          <div>
            <h2>{t("includedFeatures")}</h2>
            <p>{t("serverEnforced")}</p>
          </div>
          <Sparkles />
        </div>
        <ul>
          {enabled.map((row) => (
            <li key={row.feature}>
              <Check />
              {row.feature.replaceAll("_", " ")}
            </li>
          ))}
        </ul>
      </section>
      <section className="panel billing-profile">
        <div className="panel-heading">
          <div>
            <h2>{t("billingProfile")}</h2>
            <p>{t("legalDraft")}</p>
          </div>
          <CreditCard />
        </div>
        <div className="form-grid two-columns">
          {(
            ["legal_name", "billing_email", "country_code", "tax_id"] as const
          ).map((key) => (
            <label className="field" key={key}>
              <span>{t(`profile.${key}`)}</span>
              <input
                value={props.profile[key]}
                disabled={!props.manageable}
                onChange={(event) =>
                  props.setProfile({
                    ...props.profile,
                    [key]: event.target.value,
                  })
                }
              />
            </label>
          ))}
        </div>
        {props.manageable ? (
          <button
            className="button primary"
            disabled={props.saving}
            onClick={props.save}
          >
            {t("saveProfile")}
          </button>
        ) : null}
      </section>
    </>
  );
}

type WalletPanelProps = {
  overview: Awaited<
    ReturnType<ReturnType<typeof useWorkspace>["api"]["billingWallet"]>
  >;
  transactions: Awaited<
    ReturnType<
      ReturnType<typeof useWorkspace>["api"]["billingWalletTransactions"]
    >
  >["results"];
  date: (value: string | null) => string;
  money: (amount: number, currency?: string) => string;
};

function WalletPanel({
  overview,
  transactions,
  date,
  money,
}: WalletPanelProps) {
  const t = useTranslations("billing");
  const wallet = overview.wallet;
  return (
    <>
      <div className="wallet-summary-grid">
        <article className="wallet-balance-card">
          <WalletCards aria-hidden="true" />
          <span>{t("wallet.balance")}</span>
          <strong>
            {money(wallet.available_balance_minor, wallet.currency)}
          </strong>
          <span className={`status-badge status-${wallet.status}`}>
            {t(`wallet.statuses.${wallet.status}`)}
          </span>
          {wallet.low_balance ? (
            <p className="wallet-low-balance" role="status">
              <AlertTriangle aria-hidden="true" /> {t("wallet.lowBalance")}
            </p>
          ) : null}
        </article>
        <article className="panel wallet-policy-card">
          <ShieldCheck aria-hidden="true" />
          <div>
            <h2>{t("wallet.topUpTitle")}</h2>
            <p>{t("wallet.customerReadOnly")}</p>
          </div>
        </article>
        <article className="panel wallet-open-card">
          <ReceiptText aria-hidden="true" />
          <strong>{overview.open_invoices.length}</strong>
          <span>{t("wallet.openInvoices")}</span>
        </article>
      </div>
      <section className="panel wallet-ledger">
        <div className="panel-heading">
          <div>
            <h2>{t("wallet.ledger")}</h2>
            <p>{t("wallet.ledgerHelp")}</p>
          </div>
        </div>
        {transactions.length ? (
          <div className="wallet-transaction-list">
            {transactions.map((entry) => (
              <article key={entry.id}>
                <span
                  className={`wallet-direction wallet-${entry.direction}`}
                  aria-hidden="true"
                >
                  {entry.direction === "credit" ? (
                    <ArrowDownLeft />
                  ) : (
                    <ArrowUpRight />
                  )}
                </span>
                <div>
                  <strong>
                    {t(`wallet.transactionTypes.${entry.transaction_type}`)}
                  </strong>
                  <small>{date(entry.created_at)}</small>
                </div>
                <div className="wallet-transaction-amount">
                  <strong>
                    {entry.direction === "credit" ? "+" : "−"}
                    {money(entry.amount_minor, entry.currency)}
                  </strong>
                  <small>
                    {t("wallet.balanceAfter", {
                      value: money(entry.balance_after_minor, entry.currency),
                    })}
                  </small>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p className="billing-empty">{t("wallet.noTransactions")}</p>
        )}
      </section>
    </>
  );
}

function PlanCatalog({
  plans,
  currentPlanId,
  manageable,
  selected,
  select,
  preview,
  schedule,
  pending,
  date,
  money,
}: {
  plans: BillingPlan[];
  currentPlanId: string;
  manageable: boolean;
  selected: BillingPrice | null;
  select: (price: BillingPrice) => void;
  preview?: {
    current_amount_minor: number;
    target_amount_minor: number;
    effective_at: string;
    change_type: string;
    proration: string;
  };
  schedule: () => void;
  pending: boolean;
  date: (value: string | null) => string;
  money: (amount: number, currency?: string) => string;
}) {
  const t = useTranslations("billing");
  return (
    <>
      {!manageable ? (
        <div className="readonly-note">{t("readOnly")}</div>
      ) : null}
      <div className="billing-plan-grid">
        {plans.map((plan) => {
          const price = plan.prices.find((item) => item.status === "active");
          const current = plan.id === currentPlanId;
          return (
            <article
              className={current ? "billing-plan current" : "billing-plan"}
              key={plan.id}
            >
              <div>
                <span>{plan.audience.replaceAll("_", " ")}</span>
                <h2>
                  {plan.display_name} <small>v{plan.version}</small>
                </h2>
                <p>{plan.description}</p>
              </div>
              {price ? (
                <strong className="plan-price">
                  {money(price.amount_minor, price.currency)}{" "}
                  <small>/ {t(`intervals.${price.billing_interval}`)}</small>
                </strong>
              ) : null}
              <ul>
                {Object.entries(plan.feature_values)
                  .filter(([, value]) => value === true)
                  .slice(0, 8)
                  .map(([key]) => (
                    <li key={key}>
                      <Check />
                      {key.replaceAll("_", " ")}
                    </li>
                  ))}
              </ul>
              {current ? (
                <span className="button secondary disabled">
                  {t("currentPlan")}
                </span>
              ) : price && manageable ? (
                <button
                  className="button primary"
                  disabled={pending}
                  onClick={() => select(price)}
                >
                  {t("previewChange")}
                </button>
              ) : (
                <span className="sales-note">{t("contactSales")}</span>
              )}
            </article>
          );
        })}
      </div>
      {selected && preview ? (
        <div
          className="billing-preview"
          role="dialog"
          aria-modal="true"
          aria-labelledby="billing-preview-title"
        >
          <div>
            <p className="eyebrow">{t("noProration")}</p>
            <h2 id="billing-preview-title">{t("changePreview")}</h2>
            <p>{t("changeEffective", { date: date(preview.effective_at) })}</p>
          </div>
          <dl>
            <div>
              <dt>{t("today")}</dt>
              <dd>{money(preview.current_amount_minor)}</dd>
            </div>
            <div>
              <dt>{t("nextPeriod")}</dt>
              <dd>{money(preview.target_amount_minor)}</dd>
            </div>
          </dl>
          <div className="dialog-actions">
            <button
              className="button secondary"
              onClick={() => select(selected)}
            >
              {t("refreshPreview")}
            </button>
            <button
              className="button primary"
              disabled={pending}
              onClick={schedule}
            >
              {t("scheduleChange")}
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

function UsagePanel({
  rows,
  estimate,
}: {
  rows: Array<{
    meter_key: string;
    quantity: string;
    included: string | number;
    remaining: string;
    overage_estimate_minor: number;
  }>;
  estimate: string;
}) {
  const t = useTranslations("billing");
  return (
    <section className="panel billing-usage">
      <div className="panel-heading">
        <div>
          <h2>{t("realUsage")}</h2>
          <p>{t("usageHelp")}</p>
        </div>
        <Gauge />
      </div>
      {rows.length ? (
        <div className="usage-list">
          {rows.map((row) => (
            <article key={row.meter_key}>
              <div>
                <strong>{row.meter_key.replaceAll("_", " ")}</strong>
                <span>
                  {formatUsageQuantity(row.quantity)} /{" "}
                  {formatUsageQuantity(row.included)}
                </span>
              </div>
              <progress
                max="100"
                value={usagePercent(row.quantity, row.included)}
                aria-label={row.meter_key}
              />
              <small>
                {t("remaining", { value: formatUsageQuantity(row.remaining) })}
              </small>
            </article>
          ))}
        </div>
      ) : (
        <p className="billing-empty">{t("noUsage")}</p>
      )}
      <p className="estimate-note">{estimate || t("estimateLabel")}</p>
    </section>
  );
}

function InvoiceList({
  rows,
  date,
  money,
}: {
  rows: Array<{
    id: string;
    invoice_number: string;
    status: string;
    issued_at: string | null;
    created_at: string;
    total_minor: number;
    currency: string;
  }>;
  date: (value: string | null) => string;
  money: (amount: number, currency?: string) => string;
}) {
  const t = useTranslations("billing");
  return (
    <section className="panel invoice-list">
      <div className="panel-heading">
        <div>
          <h2>{t("invoiceHistory")}</h2>
          <p>{t("invoiceHistoryHelp")}</p>
        </div>
        <FileText />
      </div>
      {rows.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t("invoice")}</th>
                <th>{t("date")}</th>
                <th>{t("status")}</th>
                <th>{t("total")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.invoice_number}</td>
                  <td>{date(row.issued_at || row.created_at)}</td>
                  <td>{row.status.replaceAll("_", " ")}</td>
                  <td>{money(row.total_minor, row.currency)}</td>
                  <td>
                    <Link href={`/app/billing/invoices/${row.id}`}>
                      {t("view")}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="billing-empty">{t("noInvoices")}</p>
      )}
    </section>
  );
}

function InvoiceDetail({
  row,
  date,
  money,
}: {
  row: {
    invoice_number: string;
    status: string;
    currency: string;
    issued_at: string | null;
    due_at: string | null;
    subtotal_minor: number;
    discount_minor: number;
    tax_minor: number;
    total_minor: number;
    amount_due_minor: number;
    lines: Array<{
      id: string;
      description: string;
      quantity: string;
      unit_amount_minor: number;
      amount_minor: number;
    }>;
  };
  date: (value: string | null) => string;
  money: (amount: number, currency?: string) => string;
}) {
  const t = useTranslations("billing");
  return (
    <article className="panel invoice-detail">
      <div className="invoice-title">
        <div>
          <p className="eyebrow">{t("invoice")}</p>
          <h2>{row.invoice_number}</h2>
          <span>{t("issuedOn", { date: date(row.issued_at) })}</span>
        </div>
        <span className={`status-badge status-${row.status}`}>
          {row.status}
        </span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>{t("descriptionLabel")}</th>
              <th>{t("quantity")}</th>
              <th>{t("unitPrice")}</th>
              <th>{t("amount")}</th>
            </tr>
          </thead>
          <tbody>
            {row.lines.map((line) => (
              <tr key={line.id}>
                <td>{line.description}</td>
                <td>{line.quantity}</td>
                <td>{money(line.unit_amount_minor, row.currency)}</td>
                <td>{money(line.amount_minor, row.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <dl className="invoice-totals">
        <div>
          <dt>{t("subtotal")}</dt>
          <dd>{money(row.subtotal_minor, row.currency)}</dd>
        </div>
        <div>
          <dt>{t("discount")}</dt>
          <dd>{money(row.discount_minor, row.currency)}</dd>
        </div>
        <div>
          <dt>{t("taxNotCalculated")}</dt>
          <dd>{money(row.tax_minor, row.currency)}</dd>
        </div>
        <div className="total">
          <dt>{t("total")}</dt>
          <dd>{money(row.total_minor, row.currency)}</dd>
        </div>
      </dl>
      <p className="invoice-retention">
        <ShieldCheck />
        {t("financialRecordNotice")}
      </p>
      <button className="button secondary" onClick={() => window.print()}>
        <Download />
        {t("print")}
      </button>
    </article>
  );
}
