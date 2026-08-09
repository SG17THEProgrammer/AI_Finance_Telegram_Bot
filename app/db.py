from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

from app.config import DATABASE_URL

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    first_name = Column(String, nullable=True)
    role = Column(String, nullable=True)          # e.g. Investor, Analyst
    sectors = Column(Text, nullable=True)          # comma separated for now
    watchlist = Column(Text, nullable=True)         # comma separated tickers
    briefing_time = Column(String, nullable=True)   # e.g. "08:00"
    language_pref = Column(String, nullable=True)   # detected/preferred language
    onboarded = Column(Integer, default=0)           # 0/1 flag
    pending_transcript = Column(Text, nullable=True)  # awaiting yes/no confirmation from a voice message
    google_refresh_token = Column(Text, nullable=True)  # Google Sheets OAuth - present once connected
    last_briefing_date = Column(String, nullable=True)  # "YYYY-MM-DD" - prevents duplicate daily sends
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)   # "user" or "assistant"
    content = Column(Text, nullable=False)
    # what the assistant was expecting as a reply when it sent its last message
    # (used to ground the next user turn - e.g. "awaiting_ticker")
    expected_intent = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_or_create_user(db, telegram_id: str, first_name: str = None) -> User:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if not user:
        user = User(telegram_id=telegram_id, first_name=first_name)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def save_message(db, telegram_id: str, role: str, content: str, expected_intent: str = None):
    msg = Message(telegram_id=telegram_id, role=role, content=content, expected_intent=expected_intent)
    db.add(msg)
    db.commit()
    return msg


def get_recent_history(db, telegram_id: str, limit: int = 12):
    """Returns messages oldest->newest, most recent `limit` turns."""
    rows = (
        db.query(Message)
        .filter(Message.telegram_id == telegram_id)
        .order_by(Message.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def get_last_expected_intent(db, telegram_id: str):
    row = (
        db.query(Message)
        .filter(Message.telegram_id == telegram_id, Message.role == "assistant")
        .order_by(Message.id.desc())
        .first()
    )
    return row.expected_intent if row else None