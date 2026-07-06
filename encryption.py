import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / "twilio.env")

_raw_key = os.getenv("ENCRYPTION_KEY")
if not _raw_key:
    raise RuntimeError(
        "ENCRYPTION_KEY is not set in twilio.env.\n"
        "Generate one with:\n"
        "  python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\"\n"
        "then add  ENCRYPTION_KEY=<result>  to twilio.env"
    )

_fernet = Fernet(_raw_key.strip().encode())

# Fernet ciphertext is always URL-safe base64 and starts with this prefix.
# Used to distinguish encrypted values from legacy plaintext rows.
_FERNET_PREFIX = "gAAAAA"


def encrypt(value: str | None) -> str | None:
    """Encrypt a plaintext string. Returns None/empty unchanged."""
    if not value:
        return value
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    """Decrypt a Fernet-encrypted string.
    Passes plaintext through unchanged so rows written before encryption
    was introduced continue to display correctly (one-time migration path)."""
    if not value:
        return value
    if not value.startswith(_FERNET_PREFIX):
        return value          # legacy plaintext — return as-is
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, Exception):
        return value          # decryption failed — surface raw value rather than crash
