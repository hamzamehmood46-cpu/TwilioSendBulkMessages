import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

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

    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_by = Column(String(100), nullable=True)           # username who triggered the send
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    from_number = Column(String(20), nullable=False)
    to_number = Column(String(20), nullable=False)
    recipient_name = Column(String(255), nullable=False)
    message_body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    twilio_sid = Column(String(64), nullable=True)
    error = Column(Text, nullable=True)


class LoginLog(Base):
    """Records every authentication event: successful logins, failed attempts, and logouts."""
    __tablename__ = "login_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    logged_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    action = Column(String(30), nullable=False)   # login_success | login_failed | logout
    ip_address = Column(String(45), nullable=True)
    details = Column(Text, nullable=True)


def _migrate_message_logs():
    """Add new audit columns to message_logs if they were not present in an earlier schema."""
    new_cols = {
        "sent_by":    "NVARCHAR(100) NULL",
        "created_at": "DATETIME NULL",
        "updated_at": "DATETIME NULL",
    }
    with engine.connect() as conn:
        for col, col_def in new_cols.items():
            exists = conn.execute(
                text(
                    "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_NAME='message_logs' AND COLUMN_NAME=:col"
                ),
                {"col": col},
            ).scalar()
            if not exists:
                conn.execute(text(f"ALTER TABLE message_logs ADD {col} {col_def}"))
        conn.commit()


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_message_logs()


def log_message(
    from_number, to_number, recipient_name, message_body,
    status, twilio_sid=None, error=None, sent_by=None,
):
    now = datetime.utcnow()
    session = SessionLocal()
    try:
        session.add(MessageLog(
            sent_by=sent_by,
            from_number=from_number,
            to_number=to_number,
            recipient_name=recipient_name,
            message_body=message_body,
            status=status,
            twilio_sid=twilio_sid,
            error=error,
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
        session.add(LoginLog(action=action, ip_address=ip_address, details=details))
        session.commit()
    finally:
        session.close()
