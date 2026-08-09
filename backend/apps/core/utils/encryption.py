"""
Field-level encryption using Fernet (AES-128-CBC with HMAC-SHA256).

Usage in models:
    from core.utils.encryption import EncryptedTextField
    class MyModel(models.Model):
        secret_data = EncryptedTextField(blank=True, default='')

Environment:
    FIELD_ENCRYPTION_KEY — base64-encoded 32-byte Fernet key.
    Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import base64
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import models

logger = logging.getLogger('security.audit')

_fernet_instance = None


def _get_fernet():
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance

    key = getattr(settings, 'FIELD_ENCRYPTION_KEY', '')
    if not key:
        raise RuntimeError(
            'FIELD_ENCRYPTION_KEY is not set. '
            'Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    _fernet_instance = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet_instance


def encrypt_value(plaintext: str) -> str:
    """Encrypt a string value. Returns base64-encoded ciphertext."""
    if not plaintext:
        return ''
    f = _get_fernet()
    return f.encrypt(plaintext.encode('utf-8')).decode('utf-8')


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a base64-encoded ciphertext. Returns plaintext string."""
    if not ciphertext:
        return ''
    f = _get_fernet()
    try:
        return f.decrypt(ciphertext.encode('utf-8')).decode('utf-8')
    except InvalidToken:
        logger.error('Failed to decrypt field value — invalid token or corrupted data')
        raise ValueError('Decryption failed: invalid key or corrupted data')


def safe_decrypt_value(ciphertext: str) -> str:
    """
    Like decrypt_value but never raises — used in read-only rendering contexts
    (e.g. Django Admin change page) so that a bad/rotated key does not cause a 500.
    Returns a visible placeholder so staff knows decryption failed.
    """
    if not ciphertext:
        return ''
    try:
        return decrypt_value(ciphertext)
    except Exception as exc:
        logger.error(
            'safe_decrypt_value: could not decrypt field — %s. '
            'Check that FIELD_ENCRYPTION_KEY matches the key used to write this data.',
            type(exc).__name__,
        )
        return '[decryption error — check FIELD_ENCRYPTION_KEY]'


class EncryptedTextField(models.TextField):
    """
    A TextField that transparently encrypts data at rest using Fernet.

    Data is stored encrypted in the database and decrypted on read.
    Supports: blank, null, default, max_length.

    Protocol: Fernet (AES-128-CBC + HMAC-SHA256), 256-bit key.
    """

    def get_prep_value(self, value):
        """Encrypt before saving to database."""
        value = super().get_prep_value(value)
        if value is None or value == '':
            return value
        return encrypt_value(str(value))

    def from_db_value(self, value, expression, connection):
        """Decrypt when reading from database. Uses safe fallback to avoid 500 on key mismatch."""
        if value is None or value == '':
            return value
        return safe_decrypt_value(value)

    def to_python(self, value):
        """Handle deserialization (forms, fixtures)."""
        if value is None:
            return value
        return str(value)
