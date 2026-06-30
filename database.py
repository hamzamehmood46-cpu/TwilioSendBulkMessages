import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

load_dotenv(Path(__file__).parent / "twilio.env")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./message_logs.db")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class MessageLog(Base):
    __tablename__ = "message_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    from_number = Column(String(20), nullable=False)
    to_number = Column(String(20), nullable=False)
    recipient_name = Column(String(255), nullable=False)
    message_body = Column(Text, nullable=False)
    status = Column(String(20), nullable=False)
    twilio_sid = Column(String(64), nullable=True)
    error = Column(Text, nullable=True)


def init_db():
    Base.metadata.create_all(bind=engine)


def log_message(from_number, to_number, recipient_name, message_body, status, twilio_sid=None, error=None):
    session = SessionLocal()
    try:
        session.add(MessageLog(
            from_number=from_number,
            to_number=to_number,
            recipient_name=recipient_name,
            message_body=message_body,
            status=status,
            twilio_sid=twilio_sid,
            error=error,
        ))
        session.commit()
    finally:
        session.close()
