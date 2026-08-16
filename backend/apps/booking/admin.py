from django.contrib import admin

from booking.models import (
    Appointment,
    AppointmentEvent,
    AppointmentHold,
    AppointmentReminder,
    BookableResource,
    BookableStaffProfile,
    BookingPolicy,
    PublicBookingProfile,
    ScheduleBreak,
    ScheduleException,
    Service,
    ServiceCategory,
    StaffService,
    WaitlistEntry,
    WeeklyScheduleRule,
)


admin.site.register(
    [
        ServiceCategory,
        Service,
        BookableStaffProfile,
        StaffService,
        BookableResource,
        WeeklyScheduleRule,
        ScheduleBreak,
        ScheduleException,
        BookingPolicy,
        PublicBookingProfile,
        AppointmentHold,
        Appointment,
        AppointmentEvent,
        WaitlistEntry,
        AppointmentReminder,
    ]
)
