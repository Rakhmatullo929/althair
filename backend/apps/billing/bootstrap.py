from __future__ import annotations

import json
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.db import connection, transaction

from billing.models import PlatformCatalogState, Subscription
from billing.services import FEATURE_CATALOG, ensure_catalog
from billing.wallet import backfill_wallets
from control_plane.models import PlatformAccessStatus, PlatformRole, PlatformStaffAccess
from organizations.models import Organization


CATALOG_KEY = "platform-foundation"
CATALOG_VERSION = 1
BOOTSTRAP_ADVISORY_LOCK = 1_401_414_001


class BootstrapError(Exception):
    pass


@contextmanager
def platform_bootstrap_lock():
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [BOOTSTRAP_ADVISORY_LOCK])
    yield


def inspect_platform_bootstrap(*, owner_email: str = "") -> dict:
    owner_email = str(owner_email or "").strip().lower()
    owner = PlatformStaffAccess.objects.filter(
        role=PlatformRole.OWNER,
        status=PlatformAccessStatus.ACTIVE,
    ).select_related("user").first()
    requested_owner = None
    if owner_email:
        requested_owner = PlatformStaffAccess.objects.filter(
            user__email__iexact=owner_email,
            role=PlatformRole.OWNER,
            status=PlatformAccessStatus.ACTIVE,
        ).select_related("user").first()
    state = PlatformCatalogState.objects.filter(key=CATALOG_KEY).first()
    feature_count = len(FEATURE_CATALOG)
    present_feature_count = 0
    from billing.models import FeatureDefinition

    present_feature_count = FeatureDefinition.objects.filter(key__in=FEATURE_CATALOG).count()
    return {
        "catalog_version": state.version if state else None,
        "catalog_ready": bool(state and state.version == CATALOG_VERSION and present_feature_count == feature_count),
        "feature_count": present_feature_count,
        "expected_feature_count": feature_count,
        "active_owner_present": bool(owner),
        "requested_owner_ready": bool(requested_owner) if owner_email else None,
        "wallet_count": Organization.objects.filter(wallets__isnull=False).distinct().count(),
        "organization_count": Organization.objects.count(),
        "wallet_subscriptions": Subscription.objects.filter(
            payment_source=Subscription.PaymentSource.WALLET
        ).count(),
    }


@transaction.atomic
def bootstrap_platform(
    *,
    owner_email: str = "",
    owner_first_name: str = "",
    owner_last_name: str = "",
    owner_password: str = "",
    create_wallets: bool = False,
    migrate_subscriptions_to_wallet: bool = False,
    rotate_owner_password: bool = False,
    adopt_existing_owner: bool = False,
) -> dict:
    owner_email = str(owner_email or "").strip().lower()
    with platform_bootstrap_lock():
        ensure_catalog()
        PlatformCatalogState.objects.update_or_create(
            key=CATALOG_KEY,
            defaults={"version": CATALOG_VERSION},
        )
        created_owner = False
        rotated_password = False
        if owner_email:
            User = get_user_model()
            user = User.objects.filter(email__iexact=owner_email).first()
            access = PlatformStaffAccess.objects.filter(user=user).first() if user else None
            if user and not access and not adopt_existing_owner:
                raise BootstrapError(
                    "The owner email already belongs to a user. Use --adopt-existing-owner after verifying the account."
                )
            if not user:
                if len(owner_password) < 12:
                    raise BootstrapError("A bootstrap password of at least 12 characters is required for a new owner.")
                user = User(
                    username=owner_email,
                    email=owner_email,
                    first_name=str(owner_first_name or "")[:150],
                    last_name=str(owner_last_name or "")[:150],
                    is_active=True,
                    is_staff=False,
                    is_superuser=False,
                    must_change_password=True,
                )
                user.set_password(owner_password)
                user.full_clean()
                user.save()
                created_owner = True
            if access and access.role != PlatformRole.OWNER:
                raise BootstrapError("The existing platform access is not a platform owner account.")
            access, _ = PlatformStaffAccess.objects.get_or_create(
                user=user,
                defaults={
                    "role": PlatformRole.OWNER,
                    "status": PlatformAccessStatus.ACTIVE,
                    "mfa_required": True,
                },
            )
            changed = []
            if access.status != PlatformAccessStatus.ACTIVE:
                access.status = PlatformAccessStatus.ACTIVE
                changed.append("status")
            if not access.mfa_required:
                access.mfa_required = True
                changed.append("mfa_required")
            if changed:
                access.save(update_fields=[*changed, "updated_at"])
            if rotate_owner_password:
                if len(owner_password) < 12:
                    raise BootstrapError("A password of at least 12 characters is required for rotation.")
                user.set_password(owner_password)
                user.must_change_password = True
                user.save(update_fields=["password", "must_change_password", "updated_at"])
                rotated_password = True
            if user.is_staff or user.is_superuser:
                user.is_staff = False
                user.is_superuser = False
                user.save(update_fields=["is_staff", "is_superuser", "updated_at"])
            if hasattr(access, "mfa_device"):
                # Existing confirmed devices are preserved. Bootstrap never creates or reveals one.
                pass

        wallets_created = 0
        if create_wallets or migrate_subscriptions_to_wallet:
            wallets_created = backfill_wallets(Organization.objects.select_related("billing_account").all())
        subscriptions_migrated = 0
        if migrate_subscriptions_to_wallet:
            for subscription in Subscription.objects.select_for_update().exclude(
                payment_source=Subscription.PaymentSource.WALLET
            ):
                subscription.payment_source = Subscription.PaymentSource.WALLET
                subscription.save(update_fields=["payment_source", "updated_at"])
                subscriptions_migrated += 1
        report = inspect_platform_bootstrap(owner_email=owner_email)
        report.update(
            {
                "created_owner": created_owner,
                "rotated_password": rotated_password,
                "wallets_created": wallets_created,
                "subscriptions_migrated": subscriptions_migrated,
            }
        )
        return report


def safe_report_json(report: dict) -> str:
    return json.dumps(report, sort_keys=True, separators=(",", ":"))
