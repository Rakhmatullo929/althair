"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { BookingSlot, Conversation } from "@workspace/api-client";
import { CalendarDays, CheckCircle2, ChevronDown, Clock3 } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { localSlotLabel } from "@/lib/booking";
import { useWorkspace } from "./workspace-provider";

export function InboxBookingPanel({
  conversation,
  readOnly,
}: {
  conversation: Conversation;
  readOnly: boolean;
}) {
  const t = useTranslations("booking.inbox");
  const locale = useLocale();
  const workspace = useWorkspace();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [serviceId, setServiceId] = useState("");
  const [branchId, setBranchId] = useState("");
  const [date, setDate] = useState(() => {
    const next = new Date();
    next.setDate(next.getDate() + 1);
    return next.toISOString().slice(0, 10);
  });
  const [slots, setSlots] = useState<BookingSlot[]>([]);
  const [slot, setSlot] = useState<BookingSlot | null>(null);
  const [created, setCreated] = useState("");
  const services = useQuery({
    queryKey: ["booking", "services"],
    queryFn: () => workspace.api.bookingServices(),
    enabled: open,
  });
  const branches = useQuery({
    queryKey: ["branches", workspace.selectedOrganizationId],
    queryFn: () => workspace.api.branches(workspace.selectedOrganizationId!),
    enabled: open,
  });
  const find = useMutation({
    mutationFn: () =>
      workspace.api.bookingAvailability({
        branch_id: branchId,
        service_id: serviceId,
        date_from: date,
        date_to: date,
      }),
    onSuccess: (result) => {
      setSlots(result.results);
      setSlot(null);
    },
  });
  const create = useMutation({
    mutationFn: async () => {
      if (!slot) throw new Error(t("chooseSlot"));
      const hold = await workspace.api.createBookingHold({
        branch_id: branchId,
        service_id: serviceId,
        contact_id: conversation.contact,
        staff_profile_id: slot.staff_profile_id,
        starts_at: slot.starts_at,
      });
      return workspace.api.createBookingAppointment({
        hold_id: hold.id,
        customer_timezone: slot.timezone,
        source_conversation_id: conversation.id,
      });
    },
    onSuccess: async (appointment) => {
      setCreated(appointment.public_reference);
      setSlot(null);
      setSlots([]);
      await queryClient.invalidateQueries({ queryKey: ["booking"] });
    },
  });
  return (
    <section
      className="inbox-booking-panel"
      aria-labelledby="inbox-booking-title"
    >
      <button
        type="button"
        className="inbox-booking-toggle"
        onClick={() => setOpen(!open)}
        disabled={readOnly}
        aria-expanded={open}
      >
        <CalendarDays aria-hidden="true" />
        <span>
          <strong id="inbox-booking-title">{t("title")}</strong>
          <small>{t("description")}</small>
        </span>
        <ChevronDown aria-hidden="true" />
      </button>
      {open ? (
        <div className="inbox-booking-body">
          <div className="inbox-booking-fields">
            <label>
              <span>{t("service")}</span>
              <select
                value={serviceId}
                onChange={(event) => setServiceId(event.target.value)}
              >
                <option value="">{t("select")}</option>
                {services.data?.results.map((service) => (
                  <option value={service.id} key={service.id}>
                    {service.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>{t("branch")}</span>
              <select
                value={branchId}
                onChange={(event) => setBranchId(event.target.value)}
              >
                <option value="">{t("select")}</option>
                {branches.data?.results.map((branch) => (
                  <option value={branch.id} key={branch.id}>
                    {branch.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>{t("date")}</span>
              <input
                type="date"
                min={new Date().toISOString().slice(0, 10)}
                value={date}
                onChange={(event) => setDate(event.target.value)}
              />
            </label>
            <button
              type="button"
              className="button secondary"
              disabled={!serviceId || !branchId || find.isPending}
              onClick={() => find.mutate()}
            >
              <Clock3 />
              {t("find")}
            </button>
          </div>
          {slots.length ? (
            <div
              className="inbox-slot-list"
              role="group"
              aria-label={t("available")}
            >
              {slots.slice(0, 12).map((item) => (
                <button
                  key={`${item.starts_at}-${item.staff_profile_id}`}
                  type="button"
                  className={slot === item ? "selected" : ""}
                  onClick={() => setSlot(item)}
                >
                  {localSlotLabel(item, locale)}
                </button>
              ))}
            </div>
          ) : find.isSuccess ? (
            <p className="readonly-note">{t("none")}</p>
          ) : null}
          {slot ? (
            <div className="inbox-booking-confirm">
              <div>
                <strong>{t("confirmTitle")}</strong>
                <span>{localSlotLabel(slot, locale)}</span>
              </div>
              <button
                type="button"
                className="button primary"
                disabled={create.isPending}
                onClick={() => create.mutate()}
              >
                {t("create")}
              </button>
            </div>
          ) : null}
          {created ? (
            <div className="crm-notice" role="status">
              <CheckCircle2 />
              {t("created", { reference: created })}
            </div>
          ) : null}
          {find.error || create.error ? (
            <div className="form-alert" role="alert">
              {String(find.error ?? create.error)}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
