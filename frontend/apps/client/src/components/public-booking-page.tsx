"use client";

import {
  createApiClient,
  type BookingSlot,
  type PublicBookingPage as PublicPage,
} from "@workspace/api-client";
import {
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  MapPin,
  ShieldCheck,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, useMemo, useState } from "react";
import { localSlotLabel } from "@/lib/booking";
import { ErrorState, PageSkeleton } from "./ui";

type Step = "service" | "time" | "details" | "complete";

export function PublicBookingPage({ publicKey }: { publicKey: string }) {
  const t = useTranslations("publicBooking");
  const locale = useLocale();
  const api = useMemo(() => createApiClient(), []);
  const [page, setPage] = useState<PublicPage | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState<Step>("service");
  const [serviceId, setServiceId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [date, setDate] = useState(() => {
    const next = new Date();
    next.setDate(next.getDate() + 1);
    return next.toISOString().slice(0, 10);
  });
  const [slots, setSlots] = useState<BookingSlot[]>([]);
  const [slot, setSlot] = useState<BookingSlot | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [confirmation, setConfirmation] = useState<{
    reference: string;
    status: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    void api
      .publicBookingPage(publicKey)
      .then((result) => {
        if (active) setPage(result);
      })
      .catch((caught: Error) => {
        if (active) setError(caught.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, publicKey]);
  async function findSlots() {
    if (!serviceId || !branchId) return;
    setSubmitting(true);
    setError("");
    try {
      const response = await api.publicBookingAvailability(publicKey, {
        service_id: serviceId,
        branch_id: branchId,
        date_from: date,
        date_to: date,
      });
      setSlots(response.results);
      setStep("time");
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  async function submitDetails(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!slot) return;
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email")).trim();
    const phone = String(form.get("phone")).trim();
    if (!email && !phone) {
      const emailInput = event.currentTarget.elements.namedItem(
        "email",
      ) as HTMLInputElement;
      emailInput.setCustomValidity(t("contactRequired"));
      emailInput.reportValidity();
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const session = await api.publicBookingSession(publicKey, {
        display_name: String(form.get("display_name")),
        email,
        phone,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        language: locale as "ru" | "uz" | "en",
        consent: true,
      });
      const hold = await api.publicBookingHold(
        publicKey,
        session.session_token,
        {
          branch_id: branchId,
          service_id: serviceId,
          staff_profile_id: slot.staff_profile_id,
          starts_at: slot.starts_at,
        },
      );
      const appointment = await api.publicBookingCreate(
        publicKey,
        session.session_token,
        {
          hold_id: hold.id,
          customer_timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          customer_notes: String(form.get("notes")),
        },
      );
      setConfirmation({
        reference: appointment.public_reference,
        status: appointment.status,
      });
      setStep("complete");
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setSubmitting(false);
    }
  }
  if (loading)
    return (
      <main className="public-booking-shell">
        <PageSkeleton />
      </main>
    );
  if (error && !page)
    return (
      <main className="public-booking-shell">
        <ErrorState title={t("unavailable")} description={error} />
      </main>
    );
  if (!page) return null;
  const service = page.services.find((item) => item.id === serviceId);
  const branch = page.branches.find((item) => item.id === branchId);
  return (
    <main className="public-booking-shell">
      <section className="public-booking-card">
        <header>
          <div className="booking-mark">
            <CalendarDays />
          </div>
          <span className="eyebrow">{t("eyebrow")}</span>
          <h1>{page.title}</h1>
          <p>{page.intro_text || t("intro")}</p>
        </header>
        <ol className="booking-steps" aria-label={t("stepsLabel")}>
          {(["service", "time", "details", "complete"] as const).map(
            (item, index) => (
              <li key={item} aria-current={step === item ? "step" : undefined}>
                <span>{index + 1}</span>
                {t(`steps.${item}`)}
              </li>
            ),
          )}
        </ol>
        {error ? (
          <div className="form-alert" role="alert">
            {error}
          </div>
        ) : null}
        {step === "service" ? (
          <div className="public-booking-step">
            <h2>{t("choose")}</h2>
            <div className="public-service-grid">
              {page.services.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={serviceId === item.id ? "selected" : ""}
                  onClick={() => setServiceId(item.id)}
                >
                  <strong>{item.name}</strong>
                  <span>
                    <Clock3 />
                    {item.duration_minutes} min
                  </span>
                  <p>{item.public_description}</p>
                </button>
              ))}
            </div>
            <label className="field">
              <span>{t("branch")}</span>
              <select
                value={branchId}
                onChange={(event) => setBranchId(event.target.value)}
              >
                <option value="">{t("selectBranch")}</option>
                {page.branches.map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.name} · {item.address}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>{t("date")}</span>
              <input
                type="date"
                min={new Date().toISOString().slice(0, 10)}
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
            <button
              className="button primary"
              disabled={!serviceId || !branchId || submitting}
              onClick={() => void findSlots()}
            >
              {t("findTimes")}
            </button>
          </div>
        ) : null}
        {step === "time" ? (
          <div className="public-booking-step">
            <button className="text-button" onClick={() => setStep("service")}>
              <ChevronLeft />
              {t("back")}
            </button>
            <h2>{t("chooseTime")}</h2>
            <p>
              <MapPin />
              {branch?.name} · {branch?.timezone}
            </p>
            {slots.length ? (
              <div className="slot-grid">
                {slots.map((item) => (
                  <button
                    type="button"
                    key={`${item.starts_at}-${item.staff_profile_id}`}
                    onClick={() => {
                      setSlot(item);
                      setStep("details");
                    }}
                  >
                    {localSlotLabel(item, locale)}
                  </button>
                ))}
              </div>
            ) : (
              <div className="booking-no-slots">
                <Clock3 />
                <strong>{t("noSlots")}</strong>
                <p>{t("noSlotsHelp")}</p>
              </div>
            )}
          </div>
        ) : null}
        {step === "details" && slot ? (
          <form className="public-booking-step" onSubmit={submitDetails}>
            <button
              type="button"
              className="text-button"
              onClick={() => setStep("time")}
            >
              <ChevronLeft />
              {t("back")}
            </button>
            <h2>{t("details")}</h2>
            <div className="booking-selection">
              <strong>{service?.name}</strong>
              <span>{localSlotLabel(slot, locale)}</span>
              <small>{branch?.name}</small>
            </div>
            <div className="form-grid two">
              <label className="field">
                <span>{t("name")}</span>
                <input name="display_name" required autoComplete="name" />
              </label>
              <label className="field">
                <span>{t("email")}</span>
                <input
                  name="email"
                  type="email"
                  autoComplete="email"
                  onInput={(event) => event.currentTarget.setCustomValidity("")}
                />
              </label>
              <label className="field">
                <span>{t("phone")}</span>
                <input name="phone" type="tel" autoComplete="tel" />
              </label>
              <label className="field field-span">
                <span>{t("notes")}</span>
                <textarea name="notes" rows={3} />
              </label>
            </div>
            <label className="checkbox-field">
              <input type="checkbox" required />
              <span>{t("consent")}</span>
            </label>
            <button className="button primary" disabled={submitting}>
              {submitting ? t("reserving") : t("confirm")}
            </button>
          </form>
        ) : null}
        {step === "complete" && confirmation ? (
          <div className="booking-complete" role="status">
            <CheckCircle2 />
            <h2>{t("completeTitle")}</h2>
            <p>
              {t("completeDescription", { reference: confirmation.reference })}
            </p>
            <StatusPill status={confirmation.status} />
            <small>{t("confirmationTruth")}</small>
          </div>
        ) : null}
        <footer>
          <ShieldCheck />
          <span>{t("privacy")}</span>
          {page.privacy_url ? (
            <a href={page.privacy_url}>{t("privacyLink")}</a>
          ) : null}
        </footer>
      </section>
    </main>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className={`booking-public-status status-${status}`}>
      {status.replaceAll("_", " ")}
    </span>
  );
}
