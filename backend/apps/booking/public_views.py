from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from booking.models import Appointment, AppointmentHold, PublicBookingSession, Service
from booking.serializers import AppointmentSerializer, AppointmentHoldSerializer, AvailabilityQuerySerializer
from booking.services import (
    AppointmentHoldService,
    AppointmentService,
    AvailabilityService,
    BookingError,
    WaitlistService,
    hash_public_token,
    public_profile,
)
from crm.models import Contact, ContactIdentity, ContactIdentityType
from crm.services import add_identity, create_contact, normalize_identity
from organizations.models import Branch


def _error(exc):
    return Response({"code": exc.code, "detail": exc.message, "details": exc.details}, status=exc.status_code)


def _rate_limit(request, public_key):
    address = request.META.get("REMOTE_ADDR", "unknown")
    digest = hashlib.sha256(f"{public_key}:{address}".encode()).hexdigest()[:24]
    minute = timezone.now().strftime("%Y%m%d%H%M")
    key = f"booking:public:{digest}:{minute}"
    if cache.add(key, 1, timeout=90):
        count = 1
    else:
        count = cache.incr(key)
    if count > settings.BOOKING_MAX_PUBLIC_REQUESTS_PER_MINUTE:
        raise BookingError("rate_limit_exceeded", "Too many booking requests.", status_code=429)


def _session(request, profile):
    raw = request.headers.get("X-Booking-Session", "").strip()
    if not raw:
        raise BookingError("booking_session_required", "A booking session is required.", status_code=401)
    try:
        return PublicBookingSession.objects.select_related("contact").get(
            organization=profile.organization,
            profile=profile,
            token_hash=hash_public_token(raw),
            revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
    except PublicBookingSession.DoesNotExist as exc:
        raise BookingError("booking_session_invalid", "Booking session is invalid or expired.", status_code=401) from exc


class PublicBaseView(APIView):
    permission_classes = [AllowAny]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        try:
            _rate_limit(request, kwargs.get("public_key", "unknown"))
        except BookingError as exc:
            raise Throttled(detail=exc.message, code=exc.code) from exc


class PublicProfileView(PublicBaseView):
    def get(self, request, public_key):
        try:
            profile = public_profile(public_key)
        except BookingError as exc:
            return _error(exc)
        services = Service.objects.for_organization(profile.organization).filter(active=True).values(
            "id", "category_id", "name", "public_description", "duration_minutes", "price_minor", "currency",
            "booking_mode", "customer_can_choose_staff",
        )
        branches = Branch.objects.filter(organization=profile.organization, is_active=True).values(
            "id", "name", "address", "timezone"
        )
        return Response({
            "public_key": profile.public_key,
            "title": profile.title or profile.organization.name,
            "intro_text": profile.intro_text,
            "privacy_url": profile.privacy_url,
            "terms_url": profile.terms_url,
            "language": profile.organization.default_language,
            "services": list(services),
            "branches": list(branches),
        })


class PublicSessionView(PublicBaseView):
    def post(self, request, public_key):
        try:
            profile = public_profile(public_key)
            name = str(request.data.get("display_name", "")).strip()
            email = str(request.data.get("email", "")).strip()
            phone = str(request.data.get("phone", "")).strip()
            if not name or not (email or phone) or request.data.get("consent") is not True:
                raise BookingError(
                    "customer_details_required",
                    "Name, consent, and an email or phone number are required.",
                )
            identity_type = ContactIdentityType.EMAIL if email else ContactIdentityType.PHONE
            raw_value = email or phone
            normalized = normalize_identity(identity_type, raw_value)
            identity = ContactIdentity.objects.for_organization(profile.organization).filter(
                type=identity_type, normalized_value=normalized
            ).select_related("contact").first()
            contact = identity.contact if identity else create_contact(
                organization=profile.organization,
                membership=None,
                display_name=name,
                preferred_language=request.data.get("language", profile.organization.default_language),
                timezone=request.data.get("timezone", profile.organization.timezone),
            )
            if not identity:
                add_identity(
                    organization=profile.organization,
                    contact=contact,
                    identity_type=identity_type,
                    raw_value=raw_value,
                    is_primary=True,
                )
            token = secrets.token_urlsafe(32)
            session = PublicBookingSession.objects.create(
                organization=profile.organization,
                profile=profile,
                token_hash=hash_public_token(token),
                contact=contact,
                consented_at=timezone.now(),
                expires_at=timezone.now() + timedelta(hours=2),
            )
        except BookingError as exc:
            return _error(exc)
        return Response({"session_token": token, "expires_at": session.expires_at}, status=201)


class PublicAvailabilityView(PublicBaseView):
    def get(self, request, public_key):
        try:
            profile = public_profile(public_key)
            serializer = AvailabilityQuerySerializer(data=request.query_params)
            serializer.is_valid(raise_exception=True)
            slots = AvailabilityService(profile.organization).slots(**serializer.validated_data)
        except BookingError as exc:
            return _error(exc)
        return Response({"results": [slot.as_dict() for slot in slots]})


class PublicHoldView(PublicBaseView):
    def post(self, request, public_key):
        try:
            profile = public_profile(public_key)
            session = _session(request, profile)
            hold, created = AppointmentHoldService.create(
                organization=profile.organization,
                branch_id=request.data.get("branch_id"),
                service_id=request.data.get("service_id"),
                contact_id=session.contact_id,
                starts_at=timezone.datetime.fromisoformat(str(request.data.get("starts_at"))),
                staff_profile_id=request.data.get("staff_profile_id"),
                idempotency_key=request.headers.get("Idempotency-Key", ""),
                created_by_type=AppointmentHold.CreatedByType.CUSTOMER,
            )
        except (BookingError, TypeError, ValueError) as exc:
            if isinstance(exc, BookingError):
                return _error(exc)
            return Response({"code": "invalid_start_time"}, status=400)
        return Response(AppointmentHoldSerializer(hold).data, status=201 if created else 200)


class PublicAppointmentView(PublicBaseView):
    def post(self, request, public_key):
        try:
            profile = public_profile(public_key)
            session = _session(request, profile)
            hold = AppointmentHold.objects.for_organization(profile.organization).filter(
                pk=request.data.get("hold_id"), contact=session.contact
            ).first()
            if not hold:
                raise BookingError("hold_not_found", "Temporary reservation was not found.", status_code=404)
            appointment, created, confirmation_token = AppointmentService.create_from_hold(
                organization=profile.organization,
                hold_id=hold.id,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
                customer_timezone=request.data.get("customer_timezone", profile.organization.timezone),
                customer_notes=request.data.get("customer_notes", ""),
            )
        except BookingError as exc:
            return _error(exc)
        data = {
            "public_reference": appointment.public_reference,
            "starts_at": appointment.starts_at,
            "ends_at": appointment.ends_at,
            "status": appointment.status,
            "confirmation_status": appointment.confirmation_status,
        }
        if confirmation_token:
            data["confirmation_token"] = confirmation_token
        return Response(data, status=201 if created else 200)


class PublicAppointmentDetailView(PublicBaseView):
    def get(self, request, public_key, reference):
        try:
            profile = public_profile(public_key)
            session = _session(request, profile)
            appointment = Appointment.objects.for_organization(profile.organization).select_related(
                "branch", "service", "staff_profile", "contact"
            ).get(public_reference=reference, contact=session.contact)
        except BookingError as exc:
            return _error(exc)
        except Appointment.DoesNotExist:
            return Response({"code": "appointment_not_found"}, status=404)
        return Response({
            "public_reference": appointment.public_reference,
            "service_name": appointment.service_name_snapshot,
            "branch_name": appointment.branch.name,
            "staff_name": appointment.staff_profile.display_name if appointment.staff_profile else "",
            "starts_at": appointment.starts_at,
            "ends_at": appointment.ends_at,
            "customer_timezone": appointment.customer_timezone,
            "status": appointment.status,
            "confirmation_status": appointment.confirmation_status,
            "customer_notes": appointment.customer_notes,
        })


class PublicAppointmentActionView(PublicBaseView):
    def post(self, request, public_key, reference, action):
        try:
            profile = public_profile(public_key)
            session = _session(request, profile)
            appointment = Appointment.objects.for_organization(profile.organization).get(
                public_reference=reference, contact=session.contact
            )
            if action == "confirm":
                appointment, _ = AppointmentService.confirm(
                    organization=profile.organization,
                    appointment_id=appointment.id,
                    actor_type="customer",
                    token=request.data.get("token"),
                )
            elif action == "cancel":
                appointment, _ = AppointmentService.cancel(
                    organization=profile.organization,
                    appointment_id=appointment.id,
                    reason=request.data.get("reason", "Customer cancellation"),
                    actor_type="customer",
                    customer=True,
                )
            elif action == "reschedule":
                appointment = AppointmentService.reschedule(
                    organization=profile.organization,
                    appointment_id=appointment.id,
                    starts_at=timezone.datetime.fromisoformat(str(request.data.get("starts_at"))),
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    actor_type="customer",
                    customer=True,
                )
            else:
                raise BookingError("unknown_action", "Unknown appointment action.", status_code=404)
        except BookingError as exc:
            return _error(exc)
        except (Appointment.DoesNotExist, TypeError, ValueError):
            return Response({"code": "appointment_not_found"}, status=404)
        return Response({
            "public_reference": appointment.public_reference,
            "status": appointment.status,
            "starts_at": appointment.starts_at,
            "ends_at": appointment.ends_at,
        })


class PublicWaitlistView(PublicBaseView):
    def post(self, request, public_key):
        try:
            profile = public_profile(public_key)
            session = _session(request, profile)
            entry = WaitlistService.create(
                organization=profile.organization,
                branch_id=request.data.get("branch_id"),
                service_id=request.data.get("service_id"),
                contact_id=session.contact_id,
                earliest_date=timezone.datetime.fromisoformat(request.data["earliest_date"]).date(),
                latest_date=timezone.datetime.fromisoformat(request.data["latest_date"]).date(),
                preferred_staff_id=request.data.get("preferred_staff_id"),
                preferred_time_windows=request.data.get("preferred_time_windows", []),
            )
        except (BookingError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, BookingError):
                return _error(exc)
            return Response({"code": "invalid_waitlist_request"}, status=400)
        return Response({"id": entry.id, "status": entry.status}, status=201)
