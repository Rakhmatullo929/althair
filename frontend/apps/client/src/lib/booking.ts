import type { Appointment, BookingSlot } from "@workspace/api-client";

export function localSlotLabel(slot: BookingSlot, locale: string) {
  return new Intl.DateTimeFormat(locale, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: slot.timezone,
    timeZoneName: "short",
  }).format(new Date(slot.starts_at));
}

export function appointmentTone(status: Appointment["status"]) {
  if (["cancelled", "rejected", "no_show"].includes(status)) return "danger";
  if (["pending_confirmation"].includes(status)) return "warning";
  if (["completed"].includes(status)) return "neutral";
  return "success";
}

export function bookingDateRange(anchor: Date, days = 7) {
  const from = new Date(anchor);
  from.setHours(0, 0, 0, 0);
  const to = new Date(from);
  to.setDate(to.getDate() + days);
  return { from: from.toISOString(), to: to.toISOString() };
}

export function localDateTimeToUtc(
  localValue: string,
  timeZone: string,
  fold: 0 | 1 = 0,
) {
  const match = localValue.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
  if (!match) throw new Error("Invalid local date and time");
  const [, year, month, day, hour, minute] = match;
  const target = `${year}-${month}-${day} ${hour}:${minute}`;
  const approximate = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
  );
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const candidates: number[] = [];
  for (let shift = -15 * 60; shift <= 15 * 60; shift += 15) {
    const instant = approximate + shift * 60_000;
    const parts = Object.fromEntries(
      formatter
        .formatToParts(new Date(instant))
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );
    if (
      `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}` ===
      target
    ) {
      candidates.push(instant);
    }
  }
  const unique = [...new Set(candidates)].sort((left, right) => left - right);
  if (!unique.length)
    throw new Error("This local time does not exist in the selected timezone");
  if (fold >= unique.length)
    throw new Error("The repeated-time fold is not available");
  return new Date(unique[fold]).toISOString();
}
