import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import List, Optional

import pyotp
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, field_validator
from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

PHONE_RE = re.compile(r"^\+\d{8,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOTP_CODE_RE = re.compile(r"^\d{6}$")
SESSION_DURATION_SECONDS = 15 * 60

BASE_DIR = Path(__file__).parent
CONTACTS_FILE = BASE_DIR / "contacts.json"

load_dotenv(BASE_DIR / "twilio.env")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TOTP_SECRET = os.getenv("TOTP_SECRET")

client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI(title="Twilio Bulk SMS Demo")

# In-memory session store: {token: expires_at_epoch_seconds}
# A demo-grade session store. Restarting the backend invalidates all sessions.
_sessions: dict[str, float] = {}


def require_session(authorization: Optional[str] = Header(None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = authorization.removeprefix("Bearer ").strip()
    expires_at = _sessions.get(token)
    if expires_at is None or expires_at < time.time():
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="Session expired, please log in again")


class LoginRequest(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def valid_code(cls, v):
        v = v.strip()
        if not TOTP_CODE_RE.match(v):
            raise ValueError("code must be a 6-digit number")
        return v


class Recipient(BaseModel):
    name: str
    phone: str


class SendRequest(BaseModel):
    message: str
    recipients: List[Recipient]


class AddContactRequest(BaseModel):
    group: str
    name: str
    phone: str
    email: str = ""

    @field_validator("group", "name")
    @classmethod
    def not_blank(cls, v):
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, v):
        v = v.strip()
        if not PHONE_RE.match(v):
            raise ValueError("phone must be in E.164 format, e.g. +15551234567")
        return v

    @field_validator("email")
    @classmethod
    def valid_email(cls, v):
        v = v.strip()
        if v and not EMAIL_RE.match(v):
            raise ValueError("invalid email format")
        return v


class DeleteContactRequest(BaseModel):
    group: str
    phone: str


class BulkContactRow(BaseModel):
    group: str
    name: str
    phone: str
    email: str = ""


class BulkImportRequest(BaseModel):
    contacts: List[BulkContactRow]


def _read_contacts():
    return json.loads(CONTACTS_FILE.read_text())


def _write_contacts(data):
    CONTACTS_FILE.write_text(json.dumps(data, indent=2))


@app.post("/api/auth/login")
def login(req: LoginRequest):
    if not TOTP_SECRET:
        raise HTTPException(status_code=500, detail="TOTP_SECRET not configured. Run setup_totp.py first.")

    totp = pyotp.TOTP(TOTP_SECRET)
    if not totp.verify(req.code, valid_window=1):
        raise HTTPException(status_code=401, detail="Invalid or expired authenticator code")

    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time() + SESSION_DURATION_SECONDS
    return {"token": token, "expiresInSeconds": SESSION_DURATION_SECONDS}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    if authorization and authorization.startswith("Bearer "):
        _sessions.pop(authorization.removeprefix("Bearer ").strip(), None)
    return {"ok": True}


@app.get("/api/contacts")
def get_contacts():
    return _read_contacts()["groups"]


@app.post("/api/contacts", dependencies=[Depends(require_session)])
def add_contact(req: AddContactRequest):
    data = _read_contacts()
    group_contacts = data["groups"].setdefault(req.group, [])

    if any(c["phone"] == req.phone for c in group_contacts):
        raise HTTPException(status_code=400, detail="This phone number is already in that group")

    group_contacts.append({"name": req.name, "phone": req.phone, "email": req.email})
    _write_contacts(data)
    return data["groups"]


@app.post("/api/contacts/bulk", dependencies=[Depends(require_session)])
def bulk_add_contacts(req: BulkImportRequest):
    data = _read_contacts()
    added = []
    errors = []

    for i, row in enumerate(req.contacts, start=1):
        group = row.group.strip()
        name = row.name.strip()
        phone = row.phone.strip()
        email = row.email.strip()

        if not group or not name or not phone:
            errors.append({"row": i, "name": name, "reason": "group, name, and phone are required"})
            continue
        if not PHONE_RE.match(phone):
            errors.append({"row": i, "name": name, "reason": f"invalid phone format: {phone}"})
            continue
        if email and not EMAIL_RE.match(email):
            errors.append({"row": i, "name": name, "reason": f"invalid email format: {email}"})
            continue

        group_contacts = data["groups"].setdefault(group, [])
        if any(c["phone"] == phone for c in group_contacts):
            errors.append({"row": i, "name": name, "reason": "duplicate phone number in that group"})
            continue

        group_contacts.append({"name": name, "phone": phone, "email": email})
        added.append({"row": i, "name": name, "phone": phone, "group": group})

    _write_contacts(data)
    return {"addedCount": len(added), "errorCount": len(errors), "added": added, "errors": errors, "groups": data["groups"]}


@app.delete("/api/contacts", dependencies=[Depends(require_session)])
def delete_contact(req: DeleteContactRequest):
    data = _read_contacts()
    group_contacts = data["groups"].get(req.group)
    if group_contacts is None:
        raise HTTPException(status_code=404, detail="Group not found")

    new_contacts = [c for c in group_contacts if c["phone"] != req.phone]
    if len(new_contacts) == len(group_contacts):
        raise HTTPException(status_code=404, detail="Contact not found")

    data["groups"][req.group] = new_contacts
    _write_contacts(data)
    return data["groups"]


@app.post("/api/send", dependencies=[Depends(require_session)])
def send_messages(req: SendRequest):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    if not req.recipients:
        raise HTTPException(status_code=400, detail="At least one recipient is required")

    results = []
    for recipient in req.recipients:
        try:
            sms = client.messages.create(
                body=req.message,
                from_=TWILIO_PHONE_NUMBER,
                to=recipient.phone,
            )
            results.append({
                "name": recipient.name,
                "phone": recipient.phone,
                "status": "sent",
                "sid": sms.sid,
            })
        except TwilioRestException as err:
            results.append({
                "name": recipient.name,
                "phone": recipient.phone,
                "status": "failed",
                "error": str(err),
            })

    sent_count = sum(1 for r in results if r["status"] == "sent")
    return {"sentCount": sent_count, "total": len(results), "results": results}
