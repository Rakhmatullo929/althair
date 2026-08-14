from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from control_plane.models import PlatformMFADevice


def hash_recovery_code(code: str) -> str:
    normalized = str(code or "").strip().replace("-", "").upper()
    return hashlib.sha256(f"{settings.SECRET_KEY}|recovery|{normalized}".encode("utf-8")).hexdigest()


def generate_setup(access) -> tuple[PlatformMFADevice, str, list[str]]:
    secret = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    recovery_codes = [secrets.token_hex(5).upper() for _ in range(8)]
    device, _ = PlatformMFADevice.objects.update_or_create(
        access=access,
        defaults={
            "secret_encrypted": secret,
            "recovery_code_hashes": [hash_recovery_code(code) for code in recovery_codes],
            "last_time_step": -1,
            "enabled": False,
            "confirmed_at": None,
        },
    )
    return device, secret, recovery_codes


def provisioning_uri(access, secret: str) -> str:
    account = quote(access.user.email or access.user.username)
    issuer = quote("Althair Internal")
    return f"otpauth://totp/{issuer}:{account}?secret={secret}&issuer={issuer}&digits=6&period=30"


def totp_for(secret: str, time_step: int) -> str:
    padded = secret + "=" * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", time_step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{value:06d}"


@transaction.atomic
def verify_mfa(device: PlatformMFADevice, code: str) -> bool:
    device = PlatformMFADevice.objects.select_for_update().get(pk=device.pk)
    clean = str(code or "").strip().replace("-", "").upper()
    if settings.CONTROL_PLANE_FAKE_MFA and (settings.DEBUG or settings.TESTING) and clean == "000000":
        return True
    recovery_hash = hash_recovery_code(clean)
    if recovery_hash in device.recovery_code_hashes:
        device.recovery_code_hashes = [item for item in device.recovery_code_hashes if item != recovery_hash]
        device.save(update_fields=["recovery_code_hashes", "updated_at"])
        return True
    current_step = int(timezone.now().timestamp()) // 30
    for step in range(current_step - 1, current_step + 2):
        if step <= device.last_time_step:
            continue
        if hmac.compare_digest(totp_for(str(device.secret_encrypted), step), clean):
            device.last_time_step = step
            device.save(update_fields=["last_time_step", "updated_at"])
            return True
    return False
