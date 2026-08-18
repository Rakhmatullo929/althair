from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import Invoice, PlanPrice, Subscription, UsageAggregate, WalletTransaction
from billing.pagination import BillingPagination
from billing.providers import BillingProviderError, get_billing_provider
from billing.serializers import (
    BillingAccountSerializer,
    BillingProfileWriteSerializer,
    ChangeRequestSerializer,
    InvoiceSerializer,
    PlanSerializer,
    SubscriptionSerializer,
    UsageAggregateSerializer,
    WalletSerializer,
    WalletTransactionSerializer,
)
from billing.services import (
    BillingError,
    EntitlementService,
    METER_LIMITS,
    cancel_subscription,
    change_preview,
    checkout_state,
    ensure_billing_for_organization,
    existing_mutation,
    mutation_key,
    process_verified_event,
    remember_mutation,
    resume_subscription,
    schedule_change,
)
from control_plane.models import PlanCatalog
from organizations.models import OrganizationMembershipRole
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin


def billing_error_response(exc: BillingError):
    return Response(
        {"detail": exc.message, "code": exc.code, "details": exc.details}, status=exc.status_code
    )


class BillingBaseView(OrganizationContextMixin, APIView):
    permission_classes = [permissions.IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_settings"

    def billing(self, request):
        return ensure_billing_for_organization(request.organization)

    def require_billing_manager(self, request):
        if request.organization_membership.role not in {
            OrganizationMembershipRole.OWNER,
            OrganizationMembershipRole.ADMIN,
        }:
            raise BillingError(
                "billing_role_required", "Only an organization owner or administrator can change Billing.", status_code=403
            )


class BillingAccountView(BillingBaseView):
    def get(self, request):
        account, _, _ = self.billing(request)
        return Response(BillingAccountSerializer(account).data)

    def patch(self, request):
        try:
            self.require_billing_manager(request)
        except BillingError as exc:
            return billing_error_response(exc)
        account, _, _ = self.billing(request)
        serializer = BillingProfileWriteSerializer(account, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(BillingAccountSerializer(account).data)


class BillingSubscriptionView(BillingBaseView):
    def get(self, request):
        _, subscription, _ = self.billing(request)
        return Response(SubscriptionSerializer(subscription).data)


class BillingPlanListView(BillingBaseView):
    def get(self, request):
        account, _, _ = self.billing(request)
        plans = PlanCatalog.objects.filter(
            status=PlanCatalog.Status.ACTIVE,
            audience__in=[PlanCatalog.Audience.SELF_SERVE, PlanCatalog.Audience.SALES_ASSISTED],
            prices__status=PlanPrice.Status.ACTIVE,
            prices__currency=account.default_currency,
        ).prefetch_related("prices").distinct()
        return Response({"results": PlanSerializer(plans, many=True).data, "currency": account.default_currency})


class BillingChangePreviewView(BillingBaseView):
    def post(self, request):
        try:
            self.require_billing_manager(request)
            serializer = ChangeRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            _, subscription, _ = self.billing(request)
            price = get_object_or_404(PlanPrice.objects.select_related("plan"), pk=serializer.validated_data["price_id"])
            return Response(change_preview(subscription, price))
        except BillingError as exc:
            return billing_error_response(exc)

class BillingChangeView(BillingBaseView):
    def post(self, request):
        try:
            self.require_billing_manager(request)
            serializer = ChangeRequestSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            key_hash = mutation_key(request, "schedule_change")
            replay = existing_mutation(request.organization, "schedule_change", key_hash)
            if replay:
                return Response(replay.response_safe)
            _, subscription, _ = self.billing(request)
            price = get_object_or_404(PlanPrice.objects.select_related("plan"), pk=serializer.validated_data["price_id"])
            change = schedule_change(subscription, price, requested_by=request.user)
            response = SubscriptionSerializer(subscription).data
            remember_mutation(request.organization, "schedule_change", key_hash, result=change, response=response)
            return Response(response, status=status.HTTP_201_CREATED)
        except BillingError as exc:
            return billing_error_response(exc)
        except IntegrityError:
            replay = existing_mutation(request.organization, "schedule_change", key_hash)
            if replay:
                return Response(replay.response_safe)
            return Response({"detail": "The billing mutation conflicts with a concurrent request.", "code": "stale_conflict"}, status=409)


class BillingCancelView(BillingBaseView):
    def post(self, request):
        try:
            self.require_billing_manager(request)
            key_hash = mutation_key(request, "cancel_subscription")
            replay = existing_mutation(request.organization, "cancel_subscription", key_hash)
            if replay:
                return Response(replay.response_safe)
            _, subscription, _ = self.billing(request)
            subscription = cancel_subscription(subscription)
            response = SubscriptionSerializer(subscription).data
            remember_mutation(request.organization, "cancel_subscription", key_hash, result=subscription, response=response)
            return Response(response)
        except BillingError as exc:
            return billing_error_response(exc)


class BillingResumeView(BillingBaseView):
    def post(self, request):
        try:
            self.require_billing_manager(request)
            key_hash = mutation_key(request, "resume_subscription")
            replay = existing_mutation(request.organization, "resume_subscription", key_hash)
            if replay:
                return Response(replay.response_safe)
            _, subscription, _ = self.billing(request)
            subscription = resume_subscription(subscription)
            response = SubscriptionSerializer(subscription).data
            remember_mutation(request.organization, "resume_subscription", key_hash, result=subscription, response=response)
            return Response(response)
        except BillingError as exc:
            return billing_error_response(exc)


class BillingUsageView(BillingBaseView):
    def get(self, request):
        _, subscription, _ = self.billing(request)
        rows = UsageAggregate.objects.filter(
            organization=request.organization,
            subscription=subscription,
            period_start=subscription.current_period_start,
        ).select_related("subscription__price").order_by("meter_key")
        snapshots = EntitlementService(request.organization)
        limits = {
            meter: snapshots.resolve(limit_key).as_dict() for meter, limit_key in METER_LIMITS.items()
        }
        return Response(
            {
                "period_start": subscription.current_period_start,
                "period_end": subscription.current_period_end,
                "results": UsageAggregateSerializer(rows, many=True).data,
                "limits": limits,
                "estimate_label": "Estimated overage; tax is not calculated.",
            }
        )


class BillingInvoiceListView(BillingBaseView):
    def get(self, request):
        rows = Invoice.objects.filter(organization=request.organization).prefetch_related("lines", "payment_attempts")
        paginator = BillingPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(InvoiceSerializer(page, many=True).data)


class BillingInvoiceDetailView(BillingBaseView):
    def get(self, request, invoice_id):
        invoice = get_object_or_404(
            Invoice.objects.prefetch_related("lines", "payment_attempts"),
            pk=invoice_id,
            organization=request.organization,
        )
        return Response(InvoiceSerializer(invoice).data)


class BillingCheckoutView(BillingBaseView):
    def post(self, request):
        try:
            self.require_billing_manager(request)
            invoice = get_object_or_404(
                Invoice, pk=request.data.get("invoice_id"), organization=request.organization
            )
            return Response(checkout_state(invoice), status=200)
        except BillingError as exc:
            return billing_error_response(exc)


class BillingEntitlementsView(BillingBaseView):
    def get(self, request):
        return Response({"results": EntitlementService(request.organization).all()})


class BillingWalletView(BillingBaseView):
    """Tenant-scoped and deliberately read-only for every customer role."""

    def get(self, request):
        account, _, _ = self.billing(request)
        wallet = request.organization.wallets.get(currency=account.default_currency)
        open_invoices = Invoice.objects.filter(
            organization=request.organization,
            status=Invoice.Status.OPEN,
        ).order_by("due_at")
        return Response(
            {
                "wallet": WalletSerializer(wallet).data,
                "open_invoices": InvoiceSerializer(open_invoices[:20], many=True).data,
                "top_up_policy": "platform_admin_only",
            }
        )


class BillingWalletTransactionListView(BillingBaseView):
    def get(self, request):
        account, _, _ = self.billing(request)
        rows = WalletTransaction.objects.filter(
            organization=request.organization,
            wallet__currency=account.default_currency,
        )
        paginator = BillingPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(WalletTransactionSerializer(page, many=True).data)


class BillingWebhookView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, provider):
        if provider != "fake" or not (settings.DEBUG or settings.TESTING):
            return Response({"detail": "Billing provider webhook is unavailable."}, status=404)
        try:
            parsed = get_billing_provider(provider).parse_verified_webhook(
                payload=request.body,
                signature=request.headers.get("X-Billing-Signature", ""),
            )
            row, created = process_verified_event(provider, parsed)
            return Response({"status": row.status, "created": created}, status=200)
        except BillingProviderError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=403)
        except BillingError as exc:
            return billing_error_response(exc)
