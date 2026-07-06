import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import pyotp
import redis
from celery.result import AsyncResult
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, field_validator
from twilio.rest import Client

from celery_app import celery_app
from database import LoginLog, MessageLog, SessionLocal, init_db, log_auth_event
from tasks import send_sms_batch

PHONE_RE = re.compile(r"^\+\d{8,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TOTP_CODE_RE = re.compile(r"^\d{6}$")
SESSION_DURATION_SECONDS = 15 * 60
CONTACTS_CACHE_TTL_SECONDS = 10
PHONE_NUMBERS_CACHE_TTL_SECONDS = 300
SESSION_KEY_PREFIX = "session:"
CONTACTS_CACHE_KEY = "contacts_cache"
PHONE_NUMBERS_CACHE_KEY = "phone_numbers_cache"

EASTERN = ZoneInfo("America/New_York")

BASE_DIR = Path(__file__).parent
CONTACTS_FILE = BASE_DIR / "contacts.json"

load_dotenv(BASE_DIR / "twilio.env")

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TOTP_SECRET = os.getenv("TOTP_SECRET")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
APP_USER = os.getenv("APP_USER", "admin")

redis_client = redis.from_url(REDIS_URL, decode_responses=True)
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

app = FastAPI(title="Twilio Bulk SMS Demo")
init_db()


def _to_eastern(dt: datetime | None) -> str | None:
    """Convert a naive UTC datetime to an Eastern Time ISO string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M:%S %Z")


def require_session(authorization: Optional[str] = Header(None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()
    if not redis_client.exists(SESSION_KEY_PREFIX + token):
        raise HTTPException(status_code=401, detail="Session expired, please log in again")


def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """Validate session and return the username stored in it."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()
    raw = redis_client.get(SESSION_KEY_PREFIX + token)
    if not raw:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    try:
        return json.loads(raw).get("username", APP_USER)
    except (json.JSONDecodeError, AttributeError):
        return APP_USER


def _fetch_and_cache_phone_numbers() -> list[dict]:
    numbers = [
        {"friendlyName": n.friendly_name, "phoneNumber": n.phone_number}
        for n in twilio_client.incoming_phone_numbers.list()
    ]
    redis_client.setex(PHONE_NUMBERS_CACHE_KEY, PHONE_NUMBERS_CACHE_TTL_SECONDS, json.dumps(numbers))
    return numbers


def _get_valid_sender_numbers() -> set[str]:
    cached = redis_client.get(PHONE_NUMBERS_CACHE_KEY)
    if cached:
        return {n["phoneNumber"] for n in json.loads(cached)}
    return {n["phoneNumber"] for n in _fetch_and_cache_phone_numbers()}


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
    fromNumber: str
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
    cached = redis_client.get(CONTACTS_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)
    data = json.loads(CONTACTS_FILE.read_text())
    redis_client.setex(CONTACTS_CACHE_KEY, CONTACTS_CACHE_TTL_SECONDS, json.dumps(data))
    return data


def _write_contacts(data):
    CONTACTS_FILE.write_text(json.dumps(data, indent=2))
    redis_client.delete(CONTACTS_CACHE_KEY)


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    client_ip = request.client.host if request.client else None

    if not TOTP_SECRET:
        raise HTTPException(status_code=500, detail="TOTP_SECRET not configured. Run setup_totp.py first.")

    totp = pyotp.TOTP(TOTP_SECRET)
    if not totp.verify(req.code, valid_window=1):
        log_auth_event("login_failed", ip_address=client_ip, details="Invalid TOTP code")
        raise HTTPException(status_code=401, detail="Invalid or expired authenticator code")

    token = secrets.token_urlsafe(32)
    # Store username in the session value so get_current_user can retrieve it.
    redis_client.setex(
        SESSION_KEY_PREFIX + token,
        SESSION_DURATION_SECONDS,
        json.dumps({"username": APP_USER}),
    )
    log_auth_event("login_success", ip_address=client_ip, details=f"user={APP_USER}")
    return {"token": token, "expiresInSeconds": SESSION_DURATION_SECONDS}


@app.post("/api/auth/logout")
def logout(request: Request, authorization: Optional[str] = Header(None)):
    client_ip = request.client.host if request.client else None
    if authorization and authorization.startswith("Bearer "):
        redis_client.delete(SESSION_KEY_PREFIX + authorization.removeprefix("Bearer ").strip())
    log_auth_event("logout", ip_address=client_ip, details=f"user={APP_USER}")
    return {"ok": True}


@app.get("/api/contacts")
def get_contacts():
    return _read_contacts()["groups"]


@app.get("/api/phone-numbers")
def get_phone_numbers():
    cached = redis_client.get(PHONE_NUMBERS_CACHE_KEY)
    if cached is not None:
        return json.loads(cached)
    return _fetch_and_cache_phone_numbers()


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


@app.post("/api/send")
def send_messages(req: SendRequest, current_user: str = Depends(get_current_user)):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    if not req.recipients:
        raise HTTPException(status_code=400, detail="At least one recipient is required")

    from_number = req.fromNumber.strip()
    if not PHONE_RE.match(from_number):
        raise HTTPException(status_code=400, detail="A valid sender number is required")

    valid_numbers = _get_valid_sender_numbers()
    if valid_numbers and from_number not in valid_numbers:
        raise HTTPException(
            status_code=400,
            detail=f"Sender number {from_number} is not in your Twilio account",
        )

    recipients = [r.model_dump() for r in req.recipients]
    task = send_sms_batch.delay(req.message, from_number, recipients, current_user)
    return {"taskId": task.id, "status": "queued", "total": len(recipients)}


@app.get("/api/send/status/{task_id}", dependencies=[Depends(require_session)])
def send_status(task_id: str):
    result = AsyncResult(task_id, app=celery_app)

    if result.state in ("PENDING", "STARTED"):
        return {"status": "pending"}
    if result.state == "FAILURE":
        raise HTTPException(status_code=500, detail=str(result.result))
    if result.state == "SUCCESS":
        payload = result.result
        return {"status": "done", "sentCount": payload["sentCount"], "total": payload["total"]}
    return {"status": result.state.lower()}


@app.get("/api/logs", dependencies=[Depends(require_session)])
def get_logs(limit: int = 100):
    session = SessionLocal()
    try:
        rows = (
            session.query(MessageLog)
            .order_by(MessageLog.sent_at.desc())
            .limit(min(limit, 500))
            .all()
        )
        return [
            {
                "id": r.id,
                "sentBy": r.sent_by or "—",
                "sentAt": _to_eastern(r.sent_at),
                "createdAt": _to_eastern(r.created_at),
                "updatedAt": _to_eastern(r.updated_at),
                "fromNumber": r.from_number,
                "toNumber": r.to_number,
                "recipientName": r.recipient_name,
                "message": r.message_body,
                "status": r.status,
                "twilioSid": r.twilio_sid,
                "error": r.error,
            }
            for r in rows
        ]
    finally:
        session.close()


@app.get("/api/logs/auth", dependencies=[Depends(require_session)])
def get_auth_logs(limit: int = 100):
    session = SessionLocal()
    try:
        rows = (
            session.query(LoginLog)
            .order_by(LoginLog.logged_at.desc())
            .limit(min(limit, 500))
            .all()
        )
        return [
            {
                "id": r.id,
                "loggedAt": _to_eastern(r.logged_at),
                "action": r.action,
                "ipAddress": r.ip_address,
                "details": r.details,
            }
            for r in rows
        ]
    finally:
        session.close()
