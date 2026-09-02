from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Float
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
from app.config import DATABASE_URL, SUPABASE_DATABASE_URL

# ── Engine: Turso if configured, local SQLite as fallback ─────────────────────
if SUPABASE_DATABASE_URL:
    engine = create_engine(
        SUPABASE_DATABASE_URL,
        pool_pre_ping=True,        # detects dropped connections automatically
        pool_recycle=300,          # recycle connections every 5 min
    )
    print("[DB] Using Supabase PostgreSQL database.")
else:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )
    print("[DB] Using local SQLite database.")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
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
    preferred_markets = Column(String, nullable=True)  # comma-separated e.g. "Indian Stocks, ETFs"
    # -----------------------------

    pending_transcript = Column(Text, nullable=True)  
    google_refresh_token = Column(Text, nullable=True)  
    last_briefing_date = Column(String, nullable=True)  
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# --- NEW ALERT TABLE ---
class Alert(Base):
    __tablename__ = "alerts"
 
    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String, index=True, nullable=False)
    ticker = Column(String, nullable=False)
    alert_type = Column(String, nullable=False)      # PRICE_BELOW, PERCENT_DROP, RSI_OVERSOLD,
                                                      # TRAILING_DAYS, LAGGED_PERCENT_DROP
    target_value = Column(String, nullable=False)
    baseline_price = Column(String, nullable=True)
    baseline_date = Column(String, nullable=True)
    is_recurring = Column(Integer, default=0)
    armed = Column(Integer, default=1)
    is_active = Column(Integer, default=1)
    triggered_at = Column(DateTime, nullable=True)
    extra_config = Column(Text, nullable=True)        # ← NEW: JSON for complex alert params
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
# -----------------------


class AllowedUser(Base):
    __tablename__ = "allowed_users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    is_owner = Column(Integer, default=0)
    added_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)   
    content = Column(Text, nullable=False)
    expected_intent = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def _run_lightweight_migrations():
    """
    create_all() only creates tables that don't exist yet - it silently does
    nothing for tables that already exist but are missing newly-added
    columns (e.g. restoring an older backup, or deploying against a DB
    created before intent/risk_profile/baseline_price etc. existed). This
    adds any missing columns via ALTER TABLE ADD COLUMN, without touching
    existing data.

    SQLite's ADD COLUMN only supports adding nullable columns with no
    complex constraints - which matches every column added so far. If a
    future column needs a NOT NULL constraint or a foreign key, this simple
    approach won't be enough and a real migration tool (Alembic) should be
    used instead.
    """
    migrations = [
        # table,            column,                  sql_type
        ("users",  "intent",             "TEXT"),
        ("users",  "experience_level",   "TEXT"),
        ("users",  "investment_horizon", "TEXT"),
        ("users",  "risk_profile",       "TEXT"),
        ("users",  "primary_goal",       "TEXT"),
        ("users",  "preferred_markets",  "TEXT"),
        ("alerts", "baseline_price",     "TEXT"),
        ("alerts", "baseline_date",      "TEXT"),
        ("alerts", "is_recurring",       "INTEGER DEFAULT 0"),
        ("alerts", "armed",              "INTEGER DEFAULT 1"),
        ("alerts", "triggered_at",       "DATETIME"),
        ("alerts", "extra_config",       "TEXT"),     # ← NEW LINE ADDED HERE
    ]

    from sqlalchemy import text
    with engine.connect() as conn:
        for table, column, col_type in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                conn.commit()
                print(f"[Migration] Added column {table}.{column}")
            except Exception:
                conn.rollback()
                pass  # Column already exists — safe to ignore


def init_db():
    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()


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