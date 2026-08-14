"use client";

import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  LockKeyhole,
  ShieldCheck,
} from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Link } from "@/i18n/navigation";
import { internalApi, InternalApiError, type JsonRecord } from "@/lib/api";
import { displayValue, safeEntries } from "@/lib/presentation";
import { ReasonDialog } from "./operations-page";
import { useInternalSession } from "./admin-shell";

export function OrganizationInspector({
  organizationId,
}: {
  organizationId: string;
}) {
  const t = useTranslations();
  const me = useInternalSession();
  const [data, setData] = useState<JsonRecord | null>(null);
  const [error, setError] = useState("");
  const [action, setAction] = useState<"suspend" | "reactivate" | null>(null);
  async function load() {
    try {
      setData(
        await internalApi.get<JsonRecord>(
          `/organizations/${organizationId}/`,
          "Review organization operational configuration",
        ),
      );
    } catch (caught) {
      setError(
        caught instanceof InternalApiError
          ? caught.message
          : "Inspection failed",
      );
    }
  }
  useEffect(() => {
    let active = true;
    void internalApi
      .get<JsonRecord>(
        `/organizations/${organizationId}/`,
        "Review organization operational configuration",
      )
      .then((value) => {
        if (active) setData(value);
      })
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof InternalApiError
              ? caught.message
              : "Inspection failed",
          );
      });
    return () => {
      active = false;
    };
  }, [organizationId]);
  const canLifecycle =
    me?.role === "platform_owner" || me?.role === "platform_admin";
  if (error)
    return (
      <section className="panel error-panel">
        <AlertTriangle />
        <h1>{error}</h1>
      </section>
    );
  if (!data)
    return (
      <div className="panel loading-panel">
        <span className="spinner" />
        {t("common.loading")}
      </div>
    );
  const status = String(data.status);
  return (
    <section className="operations-page">
      <Link className="back-link" href="/app/organizations">
        <ArrowLeft />
        {t("pages.organizations")}
      </Link>
      <header className="organization-hero">
        <div className="tenant-icon">
          <Building2 />
        </div>
        <div>
          <p className="eyebrow">{t("pages.organization")}</p>
          <h1>{String(data.name)}</h1>
          <p>
            {String(data.industry)} · {String(data.timezone)} · created{" "}
            {new Date(String(data.created_at)).toLocaleDateString()}
          </p>
        </div>
        <span className={`status-pill state-${status}`}>{status}</span>
      </header>
      <div className="inspector-boundary">
        <LockKeyhole />
        <div>
          <strong>Read-only internal context</strong>
          <span>
            No customer cookie, impersonation, conversation body, transcript, or
            provider credential access.
          </span>
        </div>
        <ShieldCheck />
      </div>
      {canLifecycle ? (
        <div className="lifecycle-actions">
          {status === "suspended" ? (
            <button
              className="primary-button"
              onClick={() => setAction("reactivate")}
            >
              <CheckCircle2 />
              {t("actions.reactivate")}
            </button>
          ) : (
            <button
              className="danger-button"
              onClick={() => setAction("suspend")}
            >
              <AlertTriangle />
              {t("actions.suspend")}
            </button>
          )}
        </div>
      ) : null}
      <div
        className="inspector-tabs"
        role="tablist"
        aria-label="Organization sections"
      >
        {[
          "Overview",
          "Members",
          "Channels",
          "AI & Usage",
          "CRM Summary",
          "Provider Health",
          "Security",
          "Data Requests",
          "Audit",
          "Entitlements",
        ].map((item, index) => (
          <button key={item} role="tab" aria-selected={index === 0}>
            {item}
          </button>
        ))}
      </div>
      <div className="section-grid">
        {safeEntries(data)
          .filter(([key]) => !["id", "name"].includes(key))
          .map(([key, value]) => (
            <section className="panel inspector-section" key={key}>
              <h2>{key.replaceAll("_", " ")}</h2>
              {value && typeof value === "object" ? (
                <dl>
                  {safeEntries(value)
                    .slice(0, 20)
                    .map(([child, item]) => (
                      <div key={child}>
                        <dt>{child.replaceAll("_", " ")}</dt>
                        <dd>{displayValue(item)}</dd>
                      </div>
                    ))}
                </dl>
              ) : (
                <strong>{displayValue(value)}</strong>
              )}
            </section>
          ))}
      </div>
      {action ? (
        <ReasonDialog
          title={
            action === "suspend"
              ? t("actions.suspend")
              : t("actions.reactivate")
          }
          description={
            action === "suspend"
              ? "New logins, provider sends, and AI work will stop. Existing data remains available read-only."
              : "Only capabilities that were previously allowed will be restored."
          }
          onClose={() => setAction(null)}
          onConfirm={async (reason) => {
            await internalApi.mutate(
              `/organizations/${organizationId}/${action}/`,
              { reason },
            );
            setAction(null);
            await load();
          }}
        />
      ) : null}
    </section>
  );
}
