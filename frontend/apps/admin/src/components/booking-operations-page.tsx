"use client";

import { CalendarDays, RefreshCw, ShieldCheck } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { internalApi, InternalApiError } from "@/lib/api";

type BookingOrganization = {
  organization_id: string;
  organization_name: string;
  appointments?: number;
  failed_reminders?: number;
};

export function BookingOperationsPage() {
  const t = useTranslations("booking");
  const [rows, setRows] = useState<BookingOrganization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  async function load() {
    setLoading(true);
    setError("");
    try {
      const result = await internalApi.get<{ results: BookingOrganization[] }>(
        "/booking/overview/",
      );
      setRows(result.results);
    } catch (caught) {
      setError(
        caught instanceof InternalApiError ? caught.message : t("error"),
      );
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    let active = true;
    void internalApi
      .get<{ results: BookingOrganization[] }>("/booking/overview/")
      .then((result) => {
        if (active) setRows(result.results);
      })
      .catch((caught) => {
        if (active)
          setError(
            caught instanceof InternalApiError ? caught.message : t("error"),
          );
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [t]);
  return (
    <section className="operations-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">{t("eyebrow")}</p>
          <h1>{t("title")}</h1>
          <p>{t("description")}</p>
        </div>
        <button
          className="secondary-button compact"
          onClick={() => void load()}
        >
          <RefreshCw />
          {t("refresh")}
        </button>
      </header>
      <div className="assurance-strip">
        <ShieldCheck />
        <span>{t("assurance")}</span>
        <i />
        {t("noContent")}
      </div>
      {error ? (
        <div className="error-panel" role="alert">
          {error}
        </div>
      ) : null}
      {loading ? (
        <div className="panel loading-panel">
          <span className="spinner" />
          {t("loading")}
        </div>
      ) : (
        <section className="panel data-panel">
          <div className="panel-title">
            <div>
              <h2>{t("organizations")}</h2>
              <span>{t("stored", { count: rows.length })}</span>
            </div>
          </div>
          {rows.length ? (
            <div className="booking-admin-grid">
              {rows.map((row) => (
                <article key={row.organization_id}>
                  <span>
                    <CalendarDays />
                  </span>
                  <div>
                    <strong>{row.organization_name}</strong>
                    <small>{row.organization_id}</small>
                  </div>
                  <dl>
                    <div>
                      <dt>{t("appointments")}</dt>
                      <dd>{row.appointments ?? 0}</dd>
                    </div>
                    <div>
                      <dt>{t("failed")}</dt>
                      <dd>{row.failed_reminders ?? 0}</dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty">
              <p>{t("empty")}</p>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
