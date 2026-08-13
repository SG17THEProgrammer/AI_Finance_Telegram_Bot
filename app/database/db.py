from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float
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
    role = Column(String, nullable=True)            # e.g., Investor, Analyst
    sectors = Column(Text, nullable=True)           # comma separated
    watchlist = Column(Text, nullable=True)         # comma separated tickers
    briefing_time = Column(String, nullable=True)   # e.g., "08:00"
    language_pref = Column(String, nullable=True)   
    onboarded = Column(Integer, default=0)          # 0/1 flag
    
    # --- NEW ONBOARDING FIELDS ---
    intent = Column(String, nullable=True)             # e.g., "Long-term investing", "Stock research"
    experience_level = Column(String, nullable=True)   # Beginner, Intermediate, Advanced
    investment_horizon = Column(String, nullable=True) # < 1 year, 1-3 years, 5+ years
    risk_profile = Column(String, nullable=True)       # Conservative, Moderate, Aggressive
    primary_goal = Column(String, nullable=True)       # Wealth creation, Car buy, Retirement
    # -----------------------------

    pending_transcript = Column(Text, nullable=True)  
    google_refresh_token = Column(Text, nullable=True)  
    last_briefing_date = Column(String, nullable=True)  
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# --- NEW ALERT TABLE ---
class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, index=True, nullable=False)
    ticker = Column(String, nullable=False)          # e.g., RELIANCE, NIFTY_50
    alert_type = Column(String, nullable=False)      # PRICE_ABOVE, PRICE_BELOW, PERCENT_DROP, PERCENT_GAIN, RSI_OVERSOLD, RSI_OVERBOUGHT
    target_value = Column(String, nullable=False)    # e.g., "1400", "3" (percent, no % sign), "30" (RSI level)
    baseline_price = Column(Float, nullable=True)     # snapshot price at creation - used to evaluate PERCENT_DROP/PERCENT_GAIN
    is_active = Column(Integer, default=1)           # 1 for active, 0 for triggered/disabled
    triggered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
# -----------------------


class AllowedUser(Base):
    __tablename__ = "allowed_users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    is_owner = Column(Integer, default=0)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)   
    content = Column(Text, nullable=False)
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