import { describe, expect, it } from "vitest";
import {
  appointmentTone,
  bookingDateRange,
  localSlotLabel,
  localDateTimeToUtc,
} from "@/lib/booking";

describe("booking presentation", () => {
  it("labels exact slot instants in the branch timezone", () => {
    expect(
      localSlotLabel(
        {
          starts_at: "2026-08-16T04:00:00Z",
          ends_at: "2026-08-16T05:00:00Z",
          local_start: "2026-08-16T09:00:00+05:00",
          local_end: "2026-08-16T10:00:00+05:00",
          timezone: "Asia/Tashkent",
          fold: 0,
          staff_profile_id: "staff",
          resources: [],
        },
        "en",
      ),
    ).toContain("09:00");
  });

  it.each([
    ["confirmed", "success"],
    ["pending_confirmation", "warning"],
    ["cancelled", "danger"],
    ["completed", "neutral"],
  ])("maps %s to an honest status tone", (status, tone) => {
    expect(appointmentTone(status as never)).toBe(tone);
  });

  it("builds a bounded calendar interval", () => {
    const range = bookingDateRange(new Date("2026-08-15T10:00:00Z"), 7);
    expect(
      (new Date(range.to).getTime() - new Date(range.from).getTime()) /
        86400000,
    ).toBe(7);
  });

  it("rejects DST gaps and preserves repeated-time folds", () => {
    expect(() =>
      localDateTimeToUtc("2026-03-08T02:30", "America/New_York"),
    ).toThrow(/does not exist/);
    expect(localDateTimeToUtc("2026-11-01T01:30", "America/New_York", 0)).toBe(
      "2026-11-01T05:30:00.000Z",
    );
    expect(localDateTimeToUtc("2026-11-01T01:30", "America/New_York", 1)).toBe(
      "2026-11-01T06:30:00.000Z",
    );
  });
});
