"""
One-time setup: generates a TOTP secret for the authenticator app
(Google Authenticator, Microsoft Authenticator, Authy, etc.), saves it
into twilio.env, and writes a QR code image you scan to enroll.

Run once:
    python setup_totp.py
"""
import re
from pathlib import Path

import pyotp
import qrcode

BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / "twilio.env"
QR_FILE = BASE_DIR / "totp_qr.png"

ISSUER = "Twilio SMS Console"
ACCOUNT_NAME = "department-sms"


def upsert_env_var(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def main():
    if not ENV_FILE.exists():
        print(f"twilio.env not found at {ENV_FILE}. Create it from twilio.env.example first.")
        return

    env_text = ENV_FILE.read_text()
    existing = re.search(r"^TOTP_SECRET=(.+)$", env_text, re.MULTILINE)
    if existing and existing.group(1).strip():
        if input(
            "TOTP_SECRET already exists in twilio.env. Generate a new one and overwrite? [y/N] "
        ).strip().lower() != "y":
            print("Aborted. Existing secret left unchanged.")
            return

    secret = pyotp.random_base32()
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=ACCOUNT_NAME, issuer_name=ISSUER)

    qrcode.make(uri).save(QR_FILE)

    env_text = upsert_env_var(env_text, "TOTP_SECRET", secret)
    ENV_FILE.write_text(env_text)

    print(f"Saved TOTP_SECRET to {ENV_FILE}")
    print(f"Saved enrollment QR code to {QR_FILE}")
    print()
    print("Next steps:")
    print(f"  1. Open {QR_FILE} and scan it with Google Authenticator / Microsoft Authenticator / Authy.")
    print(f"  2. Or manually enter this secret key in your app: {secret}")
    print("  3. Restart the backend (uvicorn) so it picks up the new TOTP_SECRET.")


if __name__ == "__main__":
    main()
