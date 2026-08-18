from __future__ import annotations

import csv
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import (
    BillingProviderEvent,
    Invoice,
    OrganizationWallet,
    PlanPrice,
    Subscription,
    UsageAggregate,
    WalletTransaction,
)
from billing.pagination import BillingPagination
from billing.serializers import (
    BillingProviderEventSerializer,
    ExtendGraceSerializer,
    GrantSubscriptionSerializer,
    InvoiceSerializer,
    PlanCreateSerializer,
    PlanPriceSerializer,
    PlanSerializer,
    ReasonSerializer,
    SubscriptionSerializer,
    UsageAggregateSerializer,
    InternalWalletTransactionSerializer,
    WalletCreditSerializer,
    WalletDebitSerializer,
    WalletReconciliationSerializer,
    WalletSerializer,
)
from billing.services import (
    BillingError,
    EntitlementService,
    ensure_billing_for_organization,
    issue_invoice,
    mark_invoice_paid,
    publish_plan,
    reconcile_usage,
    sync_entitlement,
    void_invoice,
)
from billing.wallet import (
    credit_wallet,
    debit_adjustment,
    reconcile_wallet,
    retry_due_invoices,
    reverse_transaction,
    set_wallet_frozen,
)
from control_plane.authentication import PlatformSessionAuthentication
from control_plane.models import PlanCatalog
from control_plane.permissions import HasPlatformAccess, HasPlatformPermission, mfa_is_fresh, role_allows
from control_plane.services import record_audit, require_reason
from organizations.models import Organization


def error_response(exc: BillingError):
    return Response({"detail": exc.message, "code": exc.code, "details": exc.details}, status=exc.status_code)


class InternalBillingBaseView(APIView):
    authentication_classes = [PlatformSessionAuthentication]
    permission_classes = [HasPlatformAccess, HasPlatformPermission]
    platform_permission = "billing.read"

    def initial(self, request, *args, **kwargs):
        if not (settings.CONTROL_PLANE_ENABLE or settings.TESTING):
            from django.http import Http404

            raise Http404
        return super().initial(request, *args, **kwargs)

    def require_write(self, request, permission="billing.manage"):
        if not role_allows(request.platform_access.role, permission):
            raise BillingError("billing_permission_denied", "This internal role cannot perform the billing action.", status_code=403)
        if not mfa_is_fresh(request):
            raise BillingError("recent_mfa_required", "Recent MFA verification is required.", status_code=403)


class InternalPlanListView(InternalBillingBaseView):
    def get(self, request):
        rows = PlanCatalog.objects.prefetch_related("prices").all()
        return Response({"results": PlanSerializer(rows, many=True).data})

    @transaction.atomic
    def post(self, request):
        try:
            self.require_write(request)
            serializer = PlanCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            version = (
                PlanCatalog.objects.filter(key=data["key"]).order_by("-version").values_list("version", flat=True).first()
                or 0
            ) + 1
            plan = PlanCatalog.objects.create(
                key=data["key"],
                version=version,
                display_name=data["display_name"],
                description=data.get("description", ""),
                audience=data["audience"],
                feature_values=data["feature_values"],
                internal_notes=data.get("internal_notes", ""),
            )
            PlanPrice.objects.create(
                plan=plan,
                currency=data["currency"],
                billing_interval=data["billing_interval"],
                amount_minor=data["amount_minor"],
                included_usage=data.get("included_usage", {}),
                overage_rates=data.get("overage_rates", {}),
            )
            reason = require_reason(request.data.get("reason"))
            record_audit(
                request, action="billing.plan.create", target_type="billing_plan", target_id=plan.id,
                reason=reason, after={"key": plan.key, "version": plan.version, "status": plan.status},
            )
            return Response(PlanSerializer(plan).data, status=status.HTTP_201_CREATED)
        except BillingError as exc:
            return error_response(exc)


class InternalPlanDetailView(InternalBillingBaseView):
    def get(self, request, plan_id):
        return Response(PlanSerializer(get_object_or_404(PlanCatalog.objects.prefetch_related("prices"), pk=plan_id)).data)

    @transaction.atomic
    def patch(self, request, plan_id):
        try:
            self.require_write(request)
            plan = get_object_or_404(PlanCatalog.objects.select_for_update(), pk=plan_id)
            if plan.status != PlanCatalog.Status.DRAFT:
                raise BillingError("published_plan_immutable", "Published plans are immutable; create a new version.")
            reason = require_reason(request.data.get("reason"))
            before = {"display_name": plan.display_name, "feature_values": plan.feature_values}
            for field in ("display_name", "description", "audience", "feature_values", "internal_notes"):
                if field in request.data:
                    setattr(plan, field, request.data[field])
            plan.save()
            record_audit(
                request, action="billing.plan.update", target_type="billing_plan", target_id=plan.id,
                reason=reason, before=before, after={"display_name": plan.display_name, "feature_values": plan.feature_values},
            )
            return Response(PlanSerializer(plan).data)
        except BillingError as exc:
            return error_response(exc)


class InternalPlanPublishView(InternalBillingBaseView):
    @transaction.atomic
    def post(self, request, plan_id):
        try:
            self.require_write(request)
            reason = require_reason(request.data.get("reason"))
            plan = publish_plan(get_object_or_404(PlanCatalog, pk=plan_id))
            record_audit(
                request, action="billing.plan.publish", target_type="billing_plan", target_id=plan.id,
                reason=reason, after={"key": plan.key, "version": plan.version, "status": plan.status},
            )
            return Response(PlanSerializer(plan).data)
        except BillingError as exc:
            return error_response(exc)


class InternalSubscriptionListView(InternalBillingBaseView):
    def get(self, request):
        rows = Subscription.objects.select_related("organization", "plan", "price", "billing_account")
        if query := request.query_params.get("query"):
            rows = rows.filter(organization__name__icontains=query)
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        paginator = BillingPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(SubscriptionSerializer(page, many=True).data)


class InternalSubscriptionDetailView(InternalBillingBaseView):
    def get(self, request, subscription_id):
        subscription = get_object_or_404(
            Subscription.objects.select_related("organization", "plan", "price", "billing_account"), pk=subscription_id
        )
        payload = SubscriptionSerializer(subscription).data
        payload["entitlements"] = EntitlementService(subscription.organization).all()
        return Response(payload)


class InternalSubscriptionGrantView(InternalBillingBaseView):
    @transaction.atomic
    def post(self, request, subscription_id):
        try:
            self.require_write(request)
            serializer = GrantSubscriptionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            data = serializer.validated_data
            subscription = get_object_or_404(
                Subscription.objects.select_for_update(), pk=subscription_id, organization_id=data["organization_id"]
            )
            price = get_object_or_404(PlanPrice.objects.select_related("plan"), pk=data["price_id"])
            if price.currency != subscription.billing_account.default_currency:
                raise BillingError("currency_mismatch", "Manual subscription currency must match the billing account.")
            before = {"status": subscription.status, "plan_id": str(subscription.plan_id)}
            now = timezone.now()
            subscription.plan = price.plan
            subscription.price = price
            subscription.provider = "manual"
            subscription.status = Subscription.Status.MANUAL
            subscription.current_period_start = now
            subscription.current_period_end = now + timedelta(days=data["period_days"])
            subscription.grace_ends_at = None
            subscription.ended_at = None
            subscription.save()
            sync_entitlement(subscription)
            record_audit(
                request, action="billing.subscription.grant", target_type="billing_subscription",
                target_id=subscription.id, organization=subscription.organization, reason=data["reason"], before=before,
                after={"status": subscription.status, "plan_id": str(subscription.plan_id), "period_end": subscription.current_period_end},
            )
            return Response(SubscriptionSerializer(subscription).data)
        except BillingError as exc:
            return error_response(exc)


class InternalSubscriptionGraceView(InternalBillingBaseView):
    @transaction.atomic
    def post(self, request, subscription_id):
        try:
            self.require_write(request)
            serializer = ExtendGraceSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            subscription = get_object_or_404(Subscription.objects.select_for_update(), pk=subscription_id)
            before = {"status": subscription.status, "grace_ends_at": subscription.grace_ends_at}
            base = max(timezone.now(), subscription.grace_ends_at or timezone.now())
            subscription.status = Subscription.Status.GRACE
            subscription.grace_ends_at = base + timedelta(days=serializer.validated_data["days"])
            subscription.save(update_fields=["status", "grace_ends_at", "updated_at"])
            sync_entitlement(subscription)
            record_audit(
                request, action="billing.subscription.extend_grace", target_type="billing_subscription",
                target_id=subscription.id, organization=subscription.organization,
                reason=serializer.validated_data["reason"], before=before,
                after={"status": subscription.status, "grace_ends_at": subscription.grace_ends_at},
            )
            return Response(SubscriptionSerializer(subscription).data)
        except BillingError as exc:
            return error_response(exc)


class InternalInvoiceListView(InternalBillingBaseView):
    def get(self, request):
        rows = Invoice.objects.select_related("organization", "subscription").prefetch_related("lines", "payment_attempts")
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        paginator = BillingPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(InvoiceSerializer(page, many=True).data)


class InternalInvoiceActionView(InternalBillingBaseView):
    @transaction.atomic
    def post(self, request, invoice_id, action):
        try:
            self.require_write(request, "billing.financial")
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            invoice = get_object_or_404(Invoice.objects.select_related("subscription", "organization"), pk=invoice_id)
            before = {"status": invoice.status, "amount_due_minor": invoice.amount_due_minor}
            if action == "issue":
                invoice = issue_invoice(invoice)
            elif action == "void":
                invoice = void_invoice(invoice)
            elif action == "mark-paid":
                invoice, _ = mark_invoice_paid(invoice, reviewed=True)
            else:
                return Response({"detail": "Unsupported invoice action."}, status=404)
            record_audit(
                request, action=f"billing.invoice.{action}", target_type="billing_invoice", target_id=invoice.id,
                organization=invoice.organization, reason=serializer.validated_data["reason"], before=before,
                after={"status": invoice.status, "amount_due_minor": invoice.amount_due_minor},
            )
            return Response(InvoiceSerializer(invoice).data)
        except BillingError as exc:
            return error_response(exc)


class InternalUsageView(InternalBillingBaseView):
    def get(self, request):
        rows = UsageAggregate.objects.select_related("organization", "subscription__price")
        if value := request.query_params.get("organization_id"):
            rows = rows.filter(organization_id=value)
        totals = rows.values("meter_key").annotate(quantity=Sum("quantity")).order_by("meter_key")
        return Response({"results": list(totals)})


class InternalUsageReconcileView(InternalBillingBaseView):
    @transaction.atomic
    def post(self, request):
        try:
            self.require_write(request, "billing.reconcile")
            serializer = ReasonSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            organization = get_object_or_404(Organization, pk=request.data.get("organization_id"))
            _, subscription, _ = ensure_billing_for_organization(organization)
            rows = reconcile_usage(organization=organization, subscription=subscription)
            record_audit(
                request, action="billing.usage.reconcile", target_type="billing_usage", target_id=organization.id,
                organization=organization, reason=serializer.validated_data["reason"],
                after={"meters": len(rows), "period_start": subscription.current_period_start},
            )
            return Response({"results": UsageAggregateSerializer(rows, many=True).data})
        except BillingError as exc:
            return error_response(exc)


class InternalProviderEventListView(InternalBillingBaseView):
    def get(self, request):
        rows = BillingProviderEvent.objects.all()
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        return Response({"results": BillingProviderEventSerializer(rows[:100], many=True).data})


class InternalWalletListView(InternalBillingBaseView):
    def get(self, request):
        rows = OrganizationWallet.objects.select_related("organization").annotate(
            transaction_count=Count("transactions")
        ).order_by("organization__name", "currency", "id")
        if query := request.query_params.get("query"):
            rows = rows.filter(organization__name__icontains=query)
        if value := request.query_params.get("status"):
            rows = rows.filter(status=value)
        if value := request.query_params.get("currency"):
            rows = rows.filter(currency=value.upper())
        paginator = BillingPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        payload = []
        for wallet in page:
            item = WalletSerializer(wallet).data
            item["organization_name"] = wallet.organization.name
            item["transaction_count"] = wallet.transaction_count
            item["recent_transactions"] = InternalWalletTransactionSerializer(
                wallet.transactions.all()[:5], many=True
            ).data
            item["open_invoice_count"] = Invoice.objects.filter(
                organization=wallet.organization,
                currency=wallet.currency,
                status=Invoice.Status.OPEN,
            ).count()
            payload.append(item)
        return paginator.get_paginated_response(payload)


class InternalWalletDetailView(InternalBillingBaseView):
    def get(self, request, wallet_id):
        wallet = get_object_or_404(
            OrganizationWallet.objects.select_related("organization"), pk=wallet_id
        )
        payload = WalletSerializer(wallet).data
        payload["organization_name"] = wallet.organization.name
        payload["transactions"] = InternalWalletTransactionSerializer(
            wallet.transactions.select_related("invoice", "performed_by_platform_staff")[:100],
            many=True,
        ).data
        payload["reconciliations"] = WalletReconciliationSerializer(
            wallet.reconciliation_runs.all()[:20], many=True
        ).data
        payload["open_invoices"] = InvoiceSerializer(
            Invoice.objects.filter(
                organization=wallet.organization,
                currency=wallet.currency,
                status=Invoice.Status.OPEN,
            ).order_by("due_at")[:50],
            many=True,
        ).data
        return Response(payload)


class InternalWalletActionView(InternalBillingBaseView):
    @transaction.atomic
    def post(self, request, wallet_id, action):
        wallet = get_object_or_404(
            OrganizationWallet.objects.select_related("organization"), pk=wallet_id
        )
        try:
            if action == "top-up":
                self.require_write(request, "billing.financial")
                serializer = WalletCreditSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                entry = credit_wallet(
                    wallet,
                    amount_minor=serializer.validated_data["amount_minor"],
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    platform_staff=request.platform_access,
                    reason=serializer.validated_data["reason"],
                    request=request,
                    payment_method=serializer.validated_data["payment_method"],
                    external_reference=serializer.validated_data.get("external_reference", ""),
                    safe_metadata=serializer.validated_data.get("safe_metadata"),
                )
                return Response(InternalWalletTransactionSerializer(entry).data, status=status.HTTP_201_CREATED)
            if action == "debit-adjustment":
                self.require_write(request, "billing.financial")
                serializer = WalletDebitSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                entry = debit_adjustment(
                    wallet,
                    amount_minor=serializer.validated_data["amount_minor"],
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    platform_staff=request.platform_access,
                    reason=serializer.validated_data["reason"],
                    request=request,
                    safe_metadata=serializer.validated_data.get("safe_metadata"),
                )
                return Response(InternalWalletTransactionSerializer(entry).data, status=status.HTTP_201_CREATED)
            if action == "reverse":
                self.require_write(request, "billing.financial")
                serializer = ReasonSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                entry = get_object_or_404(
                    WalletTransaction,
                    pk=request.data.get("transaction_id"),
                    wallet=wallet,
                )
                reversal = reverse_transaction(
                    entry,
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    platform_staff=request.platform_access,
                    reason=serializer.validated_data["reason"],
                    request=request,
                )
                return Response(InternalWalletTransactionSerializer(reversal).data, status=status.HTTP_201_CREATED)
            if action in {"freeze", "unfreeze"}:
                self.require_write(request, "billing.financial")
                serializer = ReasonSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                updated = set_wallet_frozen(
                    wallet,
                    frozen=action == "freeze",
                    platform_staff=request.platform_access,
                    reason=serializer.validated_data["reason"],
                    request=request,
                )
                return Response(WalletSerializer(updated).data)
            if action == "retry-due-invoices":
                self.require_write(request, "billing.financial")
                serializer = ReasonSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                results = retry_due_invoices(wallet.organization_id, currency=wallet.currency)
                record_audit(
                    request,
                    action="wallet.retry_due_invoices",
                    target_type="organization_wallet",
                    target_id=wallet.id,
                    organization=wallet.organization,
                    reason=serializer.validated_data["reason"],
                    after={"attempted": len(results), "paid": sum(int(item.paid) for item in results)},
                )
                return Response(
                    {
                        "attempted": len(results),
                        "paid": sum(int(item.paid) for item in results),
                        "results": [
                            {
                                "invoice_id": str(item.invoice.id),
                                "paid": item.paid,
                                "required_minor": item.required_minor,
                                "available_minor": item.available_minor,
                            }
                            for item in results
                        ],
                    }
                )
            if action == "reconcile":
                self.require_write(request, "billing.reconcile")
                serializer = ReasonSerializer(data=request.data)
                serializer.is_valid(raise_exception=True)
                run = reconcile_wallet(wallet, platform_staff=request.platform_access)
                record_audit(
                    request,
                    action="wallet.reconcile",
                    target_type="organization_wallet",
                    target_id=wallet.id,
                    organization=wallet.organization,
                    reason=serializer.validated_data["reason"],
                    after={"status": run.status, "difference_minor": run.difference_minor},
                )
                return Response(WalletReconciliationSerializer(run).data)
            return Response({"detail": "Unsupported wallet action."}, status=404)
        except BillingError as exc:
            return error_response(exc)


class InternalWalletExportView(InternalBillingBaseView):
    def get(self, request, wallet_id):
        wallet = get_object_or_404(OrganizationWallet, pk=wallet_id)
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            ["transaction_id", "created_at", "direction", "type", "amount_minor", "currency", "status", "invoice_id"]
        )
        for entry in wallet.transactions.all()[:10000]:
            writer.writerow(
                [
                    entry.id,
                    entry.created_at.isoformat(),
                    entry.direction,
                    entry.transaction_type,
                    entry.amount_minor,
                    entry.currency,
                    entry.status,
                    entry.invoice_id or "",
                ]
            )
        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="wallet-{wallet.id}-ledger.csv"'
        return response
