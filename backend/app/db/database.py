import os

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./cleanspace.db")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, connect_args=connect_args)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def ensure_schema_updates(target_engine=engine):
    """Safely add new cleanrooms columns on existing PostgreSQL or SQLite databases."""
    inspector = inspect(target_engine)
    if not inspector.has_table("cleanrooms"):
        return
    if target_engine.dialect.name == "postgresql":
        with target_engine.begin() as conn:
            conn.execute(text("ALTER TABLE cleanrooms ADD COLUMN IF NOT EXISTS capacity INTEGER DEFAULT 8"))
            conn.execute(text("ALTER TABLE cleanrooms ADD COLUMN IF NOT EXISTS iso_class VARCHAR(20) DEFAULT 'ISO 7'"))
            conn.execute(text("UPDATE cleanrooms SET capacity = 8 WHERE capacity IS NULL"))
            conn.execute(text("UPDATE cleanrooms SET iso_class = 'ISO 7' WHERE iso_class IS NULL"))
    else:
        existing_columns = {col["name"] for col in inspector.get_columns("cleanrooms")}
        with target_engine.begin() as conn:
            if "capacity" not in existing_columns:
                conn.execute(text("ALTER TABLE cleanrooms ADD COLUMN capacity INTEGER DEFAULT 8"))
            if "iso_class" not in existing_columns:
                conn.execute(text("ALTER TABLE cleanrooms ADD COLUMN iso_class VARCHAR(20) DEFAULT 'ISO 7'"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()