from datetime import datetime, timezone

from app.db.database import Base


def now():
    return datetime.now(timezone.utc)