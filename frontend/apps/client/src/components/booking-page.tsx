"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  Appointment,
  BookingService,
  BookingSlot,
} from "@workspace/api-client";
import {
  BellRing,
  CalendarDays,
  CheckCircle2,
  Clock3,
  ListChecks,
  Plus,
  Settings2,
  Sparkles,
  Users,
  Wrench,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useMemo, useState } from "react";
import { Link } from "@/i18n/navigation";
import {
  appointmentTone,
  bookingDateRange,
  localDateTimeToUtc,
} from "@/lib/booking";
import { can } from "@/lib/permissions";
import {
  EmptyState,
  ErrorState,
  PageHeading,
  PageSkeleton,
  StatusBadge,
} from "./ui";
import { useWorkspace } from "./workspace-provider";

export type BookingView =
  | "calendar"
  | "appointments"
  | "appointment"
  | "services"
  | "staff"
  | "resources"
  | "schedules"
  | "waitlist"
  | "reminders"
  | "settings";

const tabs: Array<[BookingView, string]> = [
  ["calendar", "/app/booking"],
  ["appointments", "/app/booking/appointments"],
  ["services", "/app/booking/services"],
  ["staff", "/app/booking/staff"],
  ["resources", "/app/booking/resources"],
  ["schedules", "/app/booking/schedules"],
  ["waitlist", "/app/booking/waitlist"],
  ["reminders", "/app/booking/reminders"],
  ["settings", "/app/booking/settings"],
];

export function BookingPage({
  view,
  appointmentId,
}: {
  view: BookingView;
  appointmentId?: string;
}) {
  const t = useTranslations("booking");
  return (
    <section className="booking-workspace">
      <PageHeading title={t("title")} description={t("description")} />
      <nav className="booking-tabs" aria-label={t("navigationLabel")}>
        {tabs.map(([key, href]) => (
          <Link
            key={key}
            href={href}
            aria-current={
              view === key || (view === "appointment" && key === "appointments")
                ? "page"
                : undefined
            }
          >
            {t(`tabs.${key}`)}
          </Link>
        ))}
      </nav>
      {view === "calendar" ? <CalendarView /> : null}
      {view === "appointments" ? <AppointmentsView /> : null}
      {view === "appointment" && appointmentId ? (
        <AppointmentDetail appointmentId={appointmentId} />
      ) : null}
      {view === "services" ? <ServicesView /> : null}
      {view === "staff" ? <StaffView /> : null}
      {view === "resources" ? <ResourcesView /> : null}
      {view === "schedules" ? <ScheduleView /> : null}
      {view === "waitlist" ? <WaitlistView /> : null}
      {view === "reminders" ? <RemindersView /> : null}
      {view === "settings" ? <SettingsView /> : null}
    </section>
  );
}

function CalendarView() {
  const t = useTranslations("booking");
  const locale = useLocale();
  const workspace = useWorkspace();
  const range = useMemo(() => bookingDateRange(new Date()), []);
  const dashboard = useQuery({
    queryKey: ["booking", "dashboard", workspace.selectedOrganizationId],
    queryFn: () => workspace.api.bookingDashboard(),
  });
  const appointments = useQuery({
    queryKey: ["booking", "appointments", range],
    queryFn: () =>
      workspace.api.bookingAppointments({
        starts_at__gte: range.from,
        starts_at__lt: range.to,
      }),
  });
  if (dashboard.isLoading || appointments.isLoading) return <PageSkeleton />;
  if (dashboard.error || appointments.error)
    return (
      <ErrorState
        title={t("errors.load")}
        description={String(dashboard.error ?? appointments.error)}
        onRetry={() => {
          void dashboard.refetch();
          void appointments.refetch();
        }}
      />
    );
  const data = dashboard.data!;
  return (
    <>
      <div className="booking-stat-grid" aria-label={t("summaryLabel")}>
        <BookingStat
          icon={<CalendarDays />}
          value={data.today}
          label={t("stats.today")}
        />
        <BookingStat
          icon={<Clock3 />}
          value={data.next_seven_days}
          label={t("stats.week")}
        />
        <BookingStat
          icon={<CheckCircle2 />}
          value={data.pending_confirmation}
          label={t("stats.pending")}
        />
        <BookingStat
          icon={<ListChecks />}
          value={data.waitlist}
          label={t("stats.waitlist")}
        />
      </div>
      <section
        className="panel booking-calendar-panel"
        aria-labelledby="booking-week-title"
      >
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{t("calendar.eyebrow")}</span>
            <h2 id="booking-week-title">{t("calendar.title")}</h2>
          </div>
          <Link className="button secondary" href="/app/booking/appointments">
            {t("calendar.openList")}
          </Link>
        </div>
        <AppointmentCards
          rows={appointments.data?.results ?? []}
          locale={locale}
        />
      </section>
    </>
  );
}

function BookingStat({
  icon,
  value,
  label,
}: {
  icon: React.ReactNode;
  value: number;
  label: string;
}) {
  return (
    <article className="booking-stat">
      <span aria-hidden="true">{icon}</span>
      <strong>{value}</strong>
      <small>{label}</small>
    </article>
  );
}

function AppointmentCards({
  rows,
  locale,
}: {
  rows: Appointment[];
  locale: string;
}) {
  const t = useTranslations("booking");
  if (!rows.length)
    return (
      <EmptyState
        icon={<CalendarDays />}
        title={t("appointments.emptyTitle")}
        description={t("appointments.emptyDescription")}
      />
    );
  return (
    <div className="appointment-list">
      {rows.map((row) => (
        <Link
          href={`/app/booking/appointments/${row.id}`}
          key={row.id}
          className="appointment-card"
        >
          <time dateTime={row.starts_at}>
            <strong>
              {new Intl.DateTimeFormat(locale, {
                day: "2-digit",
                month: "short",
              }).format(new Date(row.starts_at))}
            </strong>
            <span>
              {new Intl.DateTimeFormat(locale, {
                hour: "2-digit",
                minute: "2-digit",
              }).format(new Date(row.starts_at))}
            </span>
          </time>
          <div>
            <strong>{row.service_name_snapshot}</strong>
            <span>
              {row.contact_name} ·{" "}
              {row.staff_name || t("appointments.anyStaff")}
            </span>
            <small>
              {row.branch_name} · {row.public_reference}
            </small>
          </div>
          <span className={`booking-tone tone-${appointmentTone(row.status)}`}>
            {t(`statuses.${row.status}`)}
          </span>
        </Link>
      ))}
    </div>
  );
}

function AppointmentsView() {
  const t = useTranslations("booking");
  const locale = useLocale();
  const workspace = useWorkspace();
  const [status, setStatus] = useState("");
  const query = useQuery({
    queryKey: ["booking", "appointments", status],
    queryFn: () => workspace.api.bookingAppointments({ status }),
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errors.load")}
        description={(query.error as Error).message}
        onRetry={() => void query.refetch()}
      />
    );
  return (
    <section className="panel">
      <div className="panel-heading booking-filter-heading">
        <div>
          <span className="eyebrow">{t("appointments.eyebrow")}</span>
          <h2>{t("appointments.title")}</h2>
        </div>
        <label className="field compact-field">
          <span>{t("appointments.filter")}</span>
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">{t("appointments.all")}</option>
            {(
              [
                "pending_confirmation",
                "confirmed",
                "checked_in",
                "in_progress",
                "completed",
                "cancelled",
                "no_show",
              ] as const
            ).map((item) => (
              <option value={item} key={item}>
                {t(`statuses.${item}`)}
              </option>
            ))}
          </select>
        </label>
      </div>
      <AppointmentCards rows={query.data?.results ?? []} locale={locale} />
    </section>
  );
}

function AppointmentDetail({ appointmentId }: { appointmentId: string }) {
  const t = useTranslations("booking");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const [rescheduling, setRescheduling] = useState(false);
  const [rescheduleDate, setRescheduleDate] = useState(() => {
    const next = new Date();
    next.setDate(next.getDate() + 1);
    return next.toISOString().slice(0, 10);
  });
  const [rescheduleSlots, setRescheduleSlots] = useState<BookingSlot[]>([]);
  const query = useQuery({
    queryKey: ["booking", "appointment", appointmentId],
    queryFn: () => workspace.api.bookingAppointment(appointmentId),
  });
  const action = useMutation({
    mutationFn: ({
      name,
      body,
    }: {
      name: "confirm" | "cancel" | "reschedule" | "status";
      body?: Record<string, string>;
    }) => workspace.api.bookingAppointmentAction(appointmentId, name, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["booking"] });
    },
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error || !query.data)
    return (
      <ErrorState
        title={t("errors.load")}
        description={(query.error as Error)?.message ?? t("errors.notFound")}
      />
    );
  const row = query.data;
  const findReschedule = async () => {
    const result = await workspace.api.bookingAvailability({
      branch_id: row.branch,
      service_id: row.service,
      date_from: rescheduleDate,
      date_to: rescheduleDate,
      staff_profile_id: row.staff_profile ?? undefined,
    });
    setRescheduleSlots(result.results);
  };
  return (
    <div className="booking-detail-grid">
      <article className="panel appointment-detail-card">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">{row.public_reference}</span>
            <h2>{row.service_name_snapshot}</h2>
          </div>
          <StatusBadge status={row.status} />
        </div>
        <dl className="booking-facts">
          <div>
            <dt>{t("detail.when")}</dt>
            <dd>
              {new Intl.DateTimeFormat(locale, {
                dateStyle: "full",
                timeStyle: "short",
              }).format(new Date(row.starts_at))}
            </dd>
          </div>
          <div>
            <dt>{t("detail.customer")}</dt>
            <dd>{row.contact_name}</dd>
          </div>
          <div>
            <dt>{t("detail.staff")}</dt>
            <dd>{row.staff_name || t("appointments.anyStaff")}</dd>
          </div>
          <div>
            <dt>{t("detail.branch")}</dt>
            <dd>{row.branch_name}</dd>
          </div>
          <div>
            <dt>{t("detail.source")}</dt>
            <dd>{row.source_channel_type || "—"}</dd>
          </div>
          <div>
            <dt>{t("detail.timezone")}</dt>
            <dd>{row.customer_timezone}</dd>
          </div>
        </dl>
        <div className="booking-actions">
          {row.status === "pending_confirmation" ? (
            <button
              className="button primary"
              onClick={() => action.mutate({ name: "confirm" })}
            >
              {t("actions.confirm")}
            </button>
          ) : null}
          {!["cancelled", "completed", "rejected", "no_show"].includes(
            row.status,
          ) ? (
            <button
              className="button secondary"
              onClick={() =>
                action.mutate({
                  name: "cancel",
                  body: { reason: t("actions.manualCancellation") },
                })
              }
            >
              {t("actions.cancel")}
            </button>
          ) : null}
          {row.status === "confirmed" ? (
            <>
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate({
                    name: "status",
                    body: { status: "checked_in" },
                  })
                }
              >
                {t("actions.checkIn")}
              </button>
              <button
                className="button secondary"
                onClick={() =>
                  action.mutate({ name: "status", body: { status: "no_show" } })
                }
              >
                {t("actions.noShow")}
              </button>
            </>
          ) : null}
          {row.status === "checked_in" ? (
            <button
              className="button secondary"
              onClick={() =>
                action.mutate({
                  name: "status",
                  body: { status: "in_progress" },
                })
              }
            >
              {t("actions.start")}
            </button>
          ) : null}
          {row.status === "in_progress" ? (
            <button
              className="button primary"
              onClick={() =>
                action.mutate({ name: "status", body: { status: "completed" } })
              }
            >
              {t("actions.complete")}
            </button>
          ) : null}
          {!["cancelled", "completed", "rejected", "no_show"].includes(
            row.status,
          ) ? (
            <button
              className="button secondary"
              onClick={() => setRescheduling(!rescheduling)}
            >
              {t("actions.reschedule")}
            </button>
          ) : null}
        </div>
        {rescheduling ? (
          <div className="booking-reschedule-panel">
            <label className="field">
              <span>{t("actions.newDate")}</span>
              <input
                type="date"
                min={new Date().toISOString().slice(0, 10)}
                value={rescheduleDate}
                onChange={(event) => setRescheduleDate(event.target.value)}
              />
            </label>
            <button
              className="button secondary"
              type="button"
              onClick={() => void findReschedule()}
            >
              {t("actions.findSlots")}
            </button>
            <div className="slot-grid">
              {rescheduleSlots.slice(0, 12).map((slot) => (
                <button
                  key={`${slot.starts_at}-${slot.staff_profile_id}`}
                  type="button"
                  onClick={() =>
                    action.mutate({
                      name: "reschedule",
                      body: { starts_at: slot.starts_at },
                    })
                  }
                >
                  {new Intl.DateTimeFormat(locale, {
                    dateStyle: "medium",
                    timeStyle: "short",
                    timeZone: slot.timezone,
                  }).format(new Date(slot.starts_at))}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {action.error ? (
          <div className="form-alert" role="alert">
            {(action.error as Error).message}
          </div>
        ) : null}
      </article>
      <aside
        className="panel appointment-timeline"
        aria-label={t("detail.timeline")}
      >
        <h2>{t("detail.timeline")}</h2>
        <ol>
          {row.events.map((event) => (
            <li key={event.id}>
              <span aria-hidden="true" />
              <div>
                <strong>{event.summary}</strong>
                <small>
                  {new Intl.DateTimeFormat(locale, {
                    dateStyle: "medium",
                    timeStyle: "short",
                  }).format(new Date(event.created_at))}{" "}
                  · {event.actor_type}
                </small>
              </div>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}

function ServicesView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const editable =
    can(workspace.membership?.role, "manage_company") &&
    workspace.membership?.organization_status !== "suspended";
  const [adding, setAdding] = useState(false);
  const [values, setValues] = useState({
    name: "",
    duration_minutes: 30,
    price_minor: "",
    currency: "UZS",
  });
  const query = useQuery({
    queryKey: ["booking", "services"],
    queryFn: () => workspace.api.bookingServices(),
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createBookingService({
        name: values.name,
        duration_minutes: values.duration_minutes,
        price_minor: values.price_minor ? Number(values.price_minor) : null,
        currency: values.price_minor ? values.currency : "",
        active: true,
      }),
    onSuccess: async () => {
      setAdding(false);
      setValues({
        name: "",
        duration_minutes: 30,
        price_minor: "",
        currency: "UZS",
      });
      await queryClient.invalidateQueries({
        queryKey: ["booking", "services"],
      });
    },
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errors.load")}
        description={(query.error as Error).message}
      />
    );
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{t("services.eyebrow")}</span>
          <h2>{t("services.title")}</h2>
        </div>
        {editable ? (
          <button className="button primary" onClick={() => setAdding(!adding)}>
            <Plus />
            {t("services.add")}
          </button>
        ) : null}
      </div>
      {adding ? (
        <form
          className="booking-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label className="field">
            <span>{t("services.name")}</span>
            <input
              required
              value={values.name}
              onChange={(event) =>
                setValues({ ...values, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>{t("services.duration")}</span>
            <input
              required
              type="number"
              min="5"
              max="1440"
              value={values.duration_minutes}
              onChange={(event) =>
                setValues({
                  ...values,
                  duration_minutes: Number(event.target.value),
                })
              }
            />
          </label>
          <label className="field">
            <span>{t("services.price")}</span>
            <input
              type="number"
              min="0"
              value={values.price_minor}
              onChange={(event) =>
                setValues({ ...values, price_minor: event.target.value })
              }
            />
          </label>
          <button className="button primary" disabled={create.isPending}>
            {t("save")}
          </button>
        </form>
      ) : null}
      <div className="booking-catalog-grid">
        {(query.data?.results ?? []).map((service) => (
          <CatalogCard key={service.id} service={service} />
        ))}
      </div>
    </section>
  );
}

function CatalogCard({ service }: { service: BookingService }) {
  const t = useTranslations("booking");
  return (
    <article className="booking-catalog-card">
      <span className="booking-icon">
        <Wrench />
      </span>
      <div>
        <strong>{service.name}</strong>
        <p>{service.public_description || t("services.noDescription")}</p>
      </div>
      <dl>
        <div>
          <dt>{t("services.duration")}</dt>
          <dd>{service.duration_minutes} min</dd>
        </div>
        <div>
          <dt>{t("services.mode")}</dt>
          <dd>{service.booking_mode.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>{t("services.price")}</dt>
          <dd>
            {service.price_minor === null
              ? t("services.notSet")
              : `${service.price_minor.toLocaleString()} ${service.currency}`}
          </dd>
        </div>
      </dl>
    </article>
  );
}

function StaffView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_company") &&
    workspace.membership?.organization_status !== "suspended";
  const [adding, setAdding] = useState(false);
  const [values, setValues] = useState({
    membership: "",
    display_name: "",
    branch: "",
    service: "",
  });
  const query = useQuery({
    queryKey: ["booking", "staff"],
    queryFn: () => workspace.api.bookingStaff(),
  });
  const members = useQuery({
    queryKey: ["memberships", organizationId],
    queryFn: () => workspace.api.memberships(organizationId),
    enabled: adding,
  });
  const branches = useQuery({
    queryKey: ["branches", organizationId],
    queryFn: () => workspace.api.branches(organizationId),
    enabled: adding,
  });
  const services = useQuery({
    queryKey: ["booking", "services"],
    queryFn: () => workspace.api.bookingServices(),
    enabled: adding,
  });
  const create = useMutation({
    mutationFn: async () => {
      const profile = await workspace.api.createBookingStaff({
        membership: values.membership,
        display_name: values.display_name,
        active: true,
        accepts_online_booking: true,
        maximum_concurrent_appointments: 1,
      });
      await workspace.api.createBookingStaffBranch({
        staff_profile: profile.id,
        branch: values.branch,
      });
      await workspace.api.createBookingStaffService({
        staff_profile: profile.id,
        service: values.service,
        active: true,
      });
      return profile;
    },
    onSuccess: async () => {
      setAdding(false);
      setValues({ membership: "", display_name: "", branch: "", service: "" });
      await queryClient.invalidateQueries({ queryKey: ["booking", "staff"] });
    },
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errors.load")}
        description={(query.error as Error).message}
      />
    );
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{t("staff.eyebrow")}</span>
          <h2>{t("staff.title")}</h2>
        </div>
        {editable ? (
          <button className="button primary" onClick={() => setAdding(!adding)}>
            <Plus />
            {t("staff.add")}
          </button>
        ) : null}
      </div>
      {adding ? (
        <form
          className="booking-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label className="field">
            <span>{t("staff.member")}</span>
            <select
              required
              value={values.membership}
              onChange={(event) =>
                setValues({ ...values, membership: event.target.value })
              }
            >
              <option value="">{t("select")}</option>
              {members.data?.results
                .filter((member) => member.status === "active")
                .map((member) => (
                  <option value={member.id} key={member.id}>
                    {member.user_name || member.user_email}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>{t("staff.name")}</span>
            <input
              required
              value={values.display_name}
              onChange={(event) =>
                setValues({ ...values, display_name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>{t("staff.branch")}</span>
            <select
              required
              value={values.branch}
              onChange={(event) =>
                setValues({ ...values, branch: event.target.value })
              }
            >
              <option value="">{t("select")}</option>
              {branches.data?.results.map((branch) => (
                <option value={branch.id} key={branch.id}>
                  {branch.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("staff.service")}</span>
            <select
              required
              value={values.service}
              onChange={(event) =>
                setValues({ ...values, service: event.target.value })
              }
            >
              <option value="">{t("select")}</option>
              {services.data?.results.map((service) => (
                <option value={service.id} key={service.id}>
                  {service.name}
                </option>
              ))}
            </select>
          </label>
          <button className="button primary" disabled={create.isPending}>
            {t("save")}
          </button>
          {create.error ? (
            <div className="form-alert" role="alert">
              {String(create.error)}
            </div>
          ) : null}
        </form>
      ) : null}
      <CollectionRows
        icon={<Users />}
        empty={t("staff.empty")}
        rows={(query.data?.results ?? []).map((staff) => ({
          id: staff.id,
          title: staff.display_name,
          description: staff.membership_name,
          badge: staff.active ? t("active") : t("inactive"),
        }))}
      />
    </section>
  );
}

function ResourcesView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_company") &&
    workspace.membership?.organization_status !== "suspended";
  const [adding, setAdding] = useState(false);
  const [values, setValues] = useState({
    branch: "",
    name: "",
    resource_type: "room",
    capacity: 1,
  });
  const query = useQuery({
    queryKey: ["booking", "resources"],
    queryFn: () => workspace.api.bookingResources(),
  });
  const branches = useQuery({
    queryKey: ["branches", organizationId],
    queryFn: () => workspace.api.branches(organizationId),
    enabled: adding,
  });
  const create = useMutation({
    mutationFn: () =>
      workspace.api.createBookingResource({ ...values, active: true }),
    onSuccess: async () => {
      setAdding(false);
      setValues({ branch: "", name: "", resource_type: "room", capacity: 1 });
      await queryClient.invalidateQueries({
        queryKey: ["booking", "resources"],
      });
    },
  });
  if (query.isLoading) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errors.load")}
        description={(query.error as Error).message}
      />
    );
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{t("resources.eyebrow")}</span>
          <h2>{t("resources.title")}</h2>
        </div>
        {editable ? (
          <button className="button primary" onClick={() => setAdding(!adding)}>
            <Plus />
            {t("resources.add")}
          </button>
        ) : null}
      </div>
      {adding ? (
        <form
          className="booking-inline-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label className="field">
            <span>{t("resources.branch")}</span>
            <select
              required
              value={values.branch}
              onChange={(event) =>
                setValues({ ...values, branch: event.target.value })
              }
            >
              <option value="">{t("select")}</option>
              {branches.data?.results.map((branch) => (
                <option key={branch.id} value={branch.id}>
                  {branch.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t("resources.name")}</span>
            <input
              required
              value={values.name}
              onChange={(event) =>
                setValues({ ...values, name: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>{t("resources.type")}</span>
            <input
              required
              value={values.resource_type}
              onChange={(event) =>
                setValues({ ...values, resource_type: event.target.value })
              }
            />
          </label>
          <label className="field">
            <span>{t("resources.capacityLabel")}</span>
            <input
              required
              type="number"
              min="1"
              max="100"
              value={values.capacity}
              onChange={(event) =>
                setValues({ ...values, capacity: Number(event.target.value) })
              }
            />
          </label>
          <button className="button primary" disabled={create.isPending}>
            {t("save")}
          </button>
          {create.error ? (
            <div className="form-alert" role="alert">
              {String(create.error)}
            </div>
          ) : null}
        </form>
      ) : null}
      <CollectionRows
        icon={<Wrench />}
        empty={t("resources.empty")}
        rows={(query.data?.results ?? []).map((resource) => ({
          id: resource.id,
          title: resource.name,
          description: resource.resource_type,
          badge: t("resources.capacity", { count: resource.capacity }),
        }))}
      />
    </section>
  );
}

function CollectionRows({
  icon,
  empty,
  rows,
}: {
  icon: React.ReactNode;
  empty: string;
  rows: Array<{
    id: string;
    title: string;
    description: string;
    badge: string;
  }>;
}) {
  return rows.length ? (
    <div className="booking-collection">
      {rows.map((row) => (
        <article key={row.id}>
          <span className="booking-icon">{icon}</span>
          <div>
            <strong>{row.title}</strong>
            <small>{row.description}</small>
          </div>
          <span>{row.badge}</span>
        </article>
      ))}
    </div>
  ) : (
    <EmptyState icon={icon} title={empty} description={empty} />
  );
}

function CollectionPanel({
  icon,
  eyebrow,
  title,
  empty,
  rows,
}: {
  icon: React.ReactNode;
  eyebrow: string;
  title: string;
  empty: string;
  rows: Array<{
    id: string;
    title: string;
    description: string;
    badge: string;
  }>;
}) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{eyebrow}</span>
          <h2>{title}</h2>
        </div>
      </div>
      <CollectionRows icon={icon} empty={empty} rows={rows} />
    </section>
  );
}

function ScheduleView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const organizationId = workspace.selectedOrganizationId!;
  const editable =
    can(workspace.membership?.role, "manage_company") &&
    workspace.membership?.organization_status !== "suspended";
  const [ownerType, setOwnerType] = useState<"branch" | "staff" | "resource">(
    "branch",
  );
  const [ownerId, setOwnerId] = useState("");
  const [weekday, setWeekday] = useState(1);
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("18:00");
  const [timeOffStart, setTimeOffStart] = useState("");
  const [timeOffEnd, setTimeOffEnd] = useState("");
  const branches = useQuery({
    queryKey: ["branches", organizationId],
    queryFn: () => workspace.api.branches(organizationId),
  });
  const staff = useQuery({
    queryKey: ["booking", "staff"],
    queryFn: () => workspace.api.bookingStaff(),
  });
  const resources = useQuery({
    queryKey: ["booking", "resources"],
    queryFn: () => workspace.api.bookingResources(),
  });
  const rules = useQuery({
    queryKey: ["booking", "schedule-rules"],
    queryFn: () => workspace.api.bookingScheduleRules(),
  });
  const exceptions = useQuery({
    queryKey: ["booking", "schedule-exceptions"],
    queryFn: () => workspace.api.bookingScheduleExceptions(),
  });
  const owners =
    ownerType === "branch"
      ? (branches.data?.results ?? []).map((row) => ({
          id: row.id,
          name: row.name,
        }))
      : ownerType === "staff"
        ? (staff.data?.results ?? []).map((row) => ({
            id: row.id,
            name: row.display_name,
          }))
        : (resources.data?.results ?? []).map((row) => ({
            id: row.id,
            name: row.name,
          }));
  const selectedResource = resources.data?.results.find(
    (row) => row.id === ownerId,
  );
  const selectedBranch = branches.data?.results.find(
    (row) =>
      row.id ===
      (ownerType === "resource" ? selectedResource?.branch : ownerId),
  );
  const selectedStaff = staff.data?.results.find((row) => row.id === ownerId);
  const ownerTimezone =
    selectedStaff?.timezone_override ||
    selectedBranch?.timezone ||
    branches.data?.results[0]?.timezone ||
    "UTC";
  const createRule = useMutation({
    mutationFn: () =>
      workspace.api.createBookingScheduleRule({
        owner_type: ownerType,
        owner_id: ownerId,
        weekday,
        start_local_time: startTime,
        end_local_time: endTime,
        effective_from: null,
        effective_to: null,
        active: true,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["booking", "schedule-rules"],
      });
    },
  });
  const createTimeOff = useMutation({
    mutationFn: () =>
      workspace.api.createBookingScheduleException({
        owner_type: ownerType,
        owner_id: ownerId,
        starts_at: localDateTimeToUtc(timeOffStart, ownerTimezone),
        ends_at: localDateTimeToUtc(timeOffEnd, ownerTimezone),
        exception_type: ownerType === "branch" ? "holiday" : "time_off",
        reason: t("schedules.timeOffReason"),
      }),
    onSuccess: async () => {
      setTimeOffStart("");
      setTimeOffEnd("");
      await queryClient.invalidateQueries({
        queryKey: ["booking", "schedule-exceptions"],
      });
    },
  });
  if (
    branches.isLoading ||
    staff.isLoading ||
    resources.isLoading ||
    rules.isLoading ||
    exceptions.isLoading
  )
    return <PageSkeleton />;
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{t("schedules.eyebrow")}</span>
          <h2>{t("schedules.title")}</h2>
        </div>
      </div>
      <div className="schedule-summary">
        <article>
          <CalendarDays />
          <strong>{t("schedules.branchHours")}</strong>
          <span>
            {t("schedules.branchCount", { count: branches.data?.count ?? 0 })}
          </span>
        </article>
        <article>
          <Users />
          <strong>{t("schedules.staffHours")}</strong>
          <span>
            {t("schedules.staffCount", { count: staff.data?.count ?? 0 })}
          </span>
        </article>
        <article>
          <Sparkles />
          <strong>{t("schedules.dst")}</strong>
          <span>{t("schedules.dstDescription")}</span>
        </article>
      </div>
      {editable ? (
        <div className="booking-schedule-editors">
          <form
            className="booking-inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              createRule.mutate();
            }}
          >
            <h3>{t("schedules.weeklyRule")}</h3>
            <label className="field">
              <span>{t("schedules.ownerType")}</span>
              <select
                value={ownerType}
                onChange={(event) => {
                  setOwnerType(event.target.value as typeof ownerType);
                  setOwnerId("");
                }}
              >
                <option value="branch">{t("schedules.branch")}</option>
                <option value="staff">{t("schedules.staff")}</option>
                <option value="resource">{t("schedules.resource")}</option>
              </select>
            </label>
            <label className="field">
              <span>{t("schedules.owner")}</span>
              <select
                required
                value={ownerId}
                onChange={(event) => setOwnerId(event.target.value)}
              >
                <option value="">{t("select")}</option>
                {owners.map((owner) => (
                  <option value={owner.id} key={owner.id}>
                    {owner.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>{t("schedules.weekday")}</span>
              <select
                value={weekday}
                onChange={(event) => setWeekday(Number(event.target.value))}
              >
                {[0, 1, 2, 3, 4, 5, 6].map((day) => (
                  <option value={day} key={day}>
                    {t(`schedules.weekdays.${day}`)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>{t("schedules.start")}</span>
              <input
                type="time"
                required
                value={startTime}
                onChange={(event) => setStartTime(event.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("schedules.end")}</span>
              <input
                type="time"
                required
                value={endTime}
                onChange={(event) => setEndTime(event.target.value)}
              />
            </label>
            <button className="button primary" disabled={createRule.isPending}>
              {t("schedules.addRule")}
            </button>
          </form>
          <form
            className="booking-inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              createTimeOff.mutate();
            }}
          >
            <h3>{t("schedules.timeOff")}</h3>
            <p className="readonly-note">
              {t("schedules.timezone", { timezone: ownerTimezone })}
            </p>
            <label className="field">
              <span>{t("schedules.startsAt")}</span>
              <input
                type="datetime-local"
                required
                value={timeOffStart}
                onChange={(event) => setTimeOffStart(event.target.value)}
              />
            </label>
            <label className="field">
              <span>{t("schedules.endsAt")}</span>
              <input
                type="datetime-local"
                required
                value={timeOffEnd}
                onChange={(event) => setTimeOffEnd(event.target.value)}
              />
            </label>
            <button
              className="button secondary"
              disabled={!ownerId || createTimeOff.isPending}
            >
              {t("schedules.addTimeOff")}
            </button>
          </form>
          {createRule.error || createTimeOff.error ? (
            <div className="form-alert" role="alert">
              {String(createRule.error ?? createTimeOff.error)}
            </div>
          ) : null}
        </div>
      ) : null}
      <div className="booking-collection">
        {(rules.data?.results ?? []).map((rule) => (
          <article key={rule.id}>
            <CalendarDays />
            <div>
              <strong>{t(`schedules.weekdays.${rule.weekday}`)}</strong>
              <small>
                {rule.start_local_time.slice(0, 5)}–
                {rule.end_local_time.slice(0, 5)}
              </small>
            </div>
            <span>{rule.owner_type}</span>
          </article>
        ))}
        {(exceptions.data?.results ?? []).map((exception) => (
          <article key={exception.id}>
            <Clock3 />
            <div>
              <strong>
                {t(`schedules.exceptions.${exception.exception_type}`)}
              </strong>
              <small>
                {new Intl.DateTimeFormat(undefined, {
                  dateStyle: "medium",
                  timeStyle: "short",
                  timeZone: ownerTimezone,
                }).format(new Date(exception.starts_at))}
              </small>
            </div>
            <span>{exception.owner_type}</span>
          </article>
        ))}
      </div>
      <p className="readonly-note">{t("schedules.help")}</p>
    </section>
  );
}

function WaitlistView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: ["booking", "waitlist"],
    queryFn: () => workspace.api.bookingWaitlist(),
  });
  if (query.isLoading) return <PageSkeleton />;
  return (
    <CollectionPanel
      icon={<ListChecks />}
      eyebrow={t("waitlist.eyebrow")}
      title={t("waitlist.title")}
      empty={t("waitlist.empty")}
      rows={(query.data?.results ?? []).map((row) => ({
        id: String(row.id),
        title: String(row.status),
        description: `${String(row.earliest_date)} — ${String(row.latest_date)}`,
        badge: String(row.status),
      }))}
    />
  );
}

function RemindersView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const query = useQuery({
    queryKey: ["booking", "reminders"],
    queryFn: () => workspace.api.bookingReminders(),
  });
  if (query.isLoading) return <PageSkeleton />;
  return (
    <CollectionPanel
      icon={<BellRing />}
      eyebrow={t("reminders.eyebrow")}
      title={t("reminders.title")}
      empty={t("reminders.empty")}
      rows={(query.data?.results ?? []).map((row) => ({
        id: String(row.id),
        title: String(row.reminder_type),
        description: String(row.scheduled_for),
        badge: String(row.status),
      }))}
    />
  );
}

function SettingsView() {
  const t = useTranslations("booking");
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["booking", "profile"],
    queryFn: () => workspace.api.bookingProfile(),
  });
  const [draft, setDraft] = useState<{
    title: string;
    intro_text: string;
    enabled: boolean;
  } | null>(null);
  const values =
    draft ??
    (query.data
      ? {
          title: query.data.title,
          intro_text: query.data.intro_text,
          enabled: query.data.enabled,
        }
      : null);
  const save = useMutation({
    mutationFn: () => workspace.api.updateBookingProfile(values!),
    onSuccess: async () => {
      setDraft(null);
      await queryClient.invalidateQueries({ queryKey: ["booking", "profile"] });
    },
  });
  if (query.isLoading || !values) return <PageSkeleton />;
  if (query.error)
    return (
      <ErrorState
        title={t("errors.load")}
        description={(query.error as Error).message}
      />
    );
  return (
    <section className="panel booking-settings">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">{t("settings.eyebrow")}</span>
          <h2>{t("settings.title")}</h2>
        </div>
        {query.data?.enabled ? (
          <Link
            className="button secondary"
            href={`/book/${query.data.public_key}`}
          >
            {t("settings.preview")}
          </Link>
        ) : null}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <label className="field">
          <span>{t("settings.publicTitle")}</span>
          <input
            value={values.title}
            onChange={(event) =>
              setDraft({ ...values, title: event.target.value })
            }
          />
        </label>
        <label className="field">
          <span>{t("settings.intro")}</span>
          <textarea
            rows={4}
            value={values.intro_text}
            onChange={(event) =>
              setDraft({ ...values, intro_text: event.target.value })
            }
          />
        </label>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={values.enabled}
            onChange={(event) =>
              setDraft({ ...values, enabled: event.target.checked })
            }
          />
          <span>{t("settings.enabled")}</span>
        </label>
        <div className="booking-public-key">
          <Settings2 />
          <div>
            <small>{t("settings.publicKey")}</small>
            <code>{query.data?.public_key}</code>
          </div>
        </div>
        <button className="button primary" disabled={save.isPending}>
          {t("save")}
        </button>
      </form>
    </section>
  );
}
