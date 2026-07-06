import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from encryption import encrypt, decrypt  # noqa: E402 — must come after load_dotenv

load_dotenv(Path(__file__).parent / "twilio.env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. "
        "Set it in twilio.env to a SQL Server or PostgreSQL connection string. "
        "Example (SQL Server / Windows Auth): "
        "mssql+pyodbc://localhost/TwilioSmsConsole"
        "?driver=ODBC+Driver+18+for+SQL+Server&trusted_connection=yes&TrustServerCertificate=yes"
    )

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class MessageLog(Base):
    __tablename__ = "message_logs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    sent_by        = Column(String(100),  nullable=True)   # username — not PII, kept plaintext
    sent_at        = Column(DateTime,     default=datetime.utcnow, nullable=False)
    created_at     = Column(DateTime,     default=datetime.utcnow, nullable=False)
    updated_at     = Column(DateTime,     default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    from_number    = Column(String(500),  nullable=False)  # encrypted
    to_number      = Column(String(500),  nullable=False)  # encrypted
    recipient_name = Column(String(500),  nullable=False)  # encrypted
    message_body   = Column(Text,         nullable=False)  # encrypted
    status         = Column(String(20),   nullable=False)  # plaintext: sent | failed
    twilio_sid     = Column(String(64),   nullable=True)   # plaintext — not PII
    error          = Column(Text,         nullable=True)   # encrypted


class LoginLog(Base):
    __tablename__ = "login_logs"

    id         = Column(Integer,     primary_key=True, autoincrement=True)
    logged_at  = Column(DateTime,    default=datetime.utcnow, nullable=False)
    action     = Column(String(30),  nullable=False)   # plaintext: login_success | login_failed | logout
    ip_address = Column(String(500), nullable=True)    # encrypted
    details    = Column(Text,        nullable=True)    # encrypted


def _migrate_db():
    """
    Idempotent migration that runs on every startup:
      1. Add new audit columns to message_logs if missing.
      2. Widen phone/name/IP columns to VARCHAR(500) so they fit Fernet ciphertext.
      3. Encrypt any existing plaintext values in both tables.
    """
    with engine.connect() as conn:

        # ── 1. Add missing audit columns to message_logs ──────────────────────
        for col, col_def in {
            "sent_by":    "NVARCHAR(100) NULL",
            "created_at": "DATETIME NULL",
            "updated_at": "DATETIME NULL",
        }.items():
            exists = conn.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME='message_logs' AND COLUMN_NAME=:col"
            ), {"col": col}).scalar()
            if not exists:
                conn.execute(text(f"ALTER TABLE message_logs ADD {col} {col_def}"))

        # ── 2. Widen columns that are still at their original narrow widths ───
        for table, col, new_def in [
            ("message_logs", "from_number",    "VARCHAR(500) NOT NULL"),
            ("message_logs", "to_number",      "VARCHAR(500) NOT NULL"),
            ("message_logs", "recipient_name", "VARCHAR(500) NOT NULL"),
            ("login_logs",   "ip_address",     "VARCHAR(500) NULL"),
        ]:
            width = conn.execute(text(
                "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME=:t AND COLUMN_NAME=:c"
            ), {"t": table, "c": col}).scalar()
            if width is not None and width < 500:
                conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {col} {new_def}"))

        conn.commit()

        # ── 3. Encrypt existing plaintext data ────────────────────────────────
        # message_logs
        rows = conn.execute(text(
            "SELECT id, from_number, to_number, recipient_name, message_body, error "
            "FROM message_logs"
        )).fetchall()
        for row in rows:
            id_, from_num, to_num, name, body, err = row
            updates = {}
            if from_num and not from_num.startswith("gAAAAA"):
                updates["from_number"] = encrypt(from_num)
            if to_num and not to_num.startswith("gAAAAA"):
                updates["to_number"] = encrypt(to_num)
            if name and not name.startswith("gAAAAA"):
                updates["recipient_name"] = encrypt(name)
            if body and not body.startswith("gAAAAA"):
                updates["message_body"] = encrypt(body)
            if err and not err.startswith("gAAAAA"):
                updates["error"] = encrypt(err)
            if updates:
                set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                updates["row_id"] = id_
                conn.execute(text(
                    f"UPDATE message_logs SET {set_clause} WHERE id = :row_id"
                ), updates)

        # login_logs
        rows = conn.execute(text(
            "SELECT id, ip_address, details FROM login_logs"
        )).fetchall()
        for row in rows:
            id_, ip, details = row
            updates = {}
            if ip and not ip.startswith("gAAAAA"):
                updates["ip_address"] = encrypt(ip)
            if details and not details.startswith("gAAAAA"):
                updates["details"] = encrypt(details)
            if updates:
                set_clause = ", ".join(f"{k} = :{k}" for k in updates)
                updates["row_id"] = id_
                conn.execute(text(
                    f"UPDATE login_logs SET {set_clause} WHERE id = :row_id"
                ), updates)

        conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_db()


def log_message(
    from_number, to_number, recipient_name, message_body,
    status, twilio_sid=None, error=None, sent_by=None,
):
    now = datetime.utcnow()
    session = SessionLocal()
    try:
        session.add(MessageLog(
            sent_by=sent_by,
            from_number=encrypt(from_number),
            to_number=encrypt(to_number),
            recipient_name=encrypt(recipient_name),
            message_body=encrypt(message_body),
            status=status,
            twilio_sid=twilio_sid,
            error=encrypt(error) if error else None,
            sent_at=now,
            created_at=now,
            updated_at=now,
        ))
        session.commit()
    finally:
        session.close()


def log_auth_event(action: str, ip_address: str = None, details: str = None):
    session = SessionLocal()
    try:
        session.add(LoginLog(
            action=action,
            ip_address=encrypt(ip_address) if ip_address else None,
            details=encrypt(details) if details else None,
        ))
        session.commit()
    finally:
        session.close()
