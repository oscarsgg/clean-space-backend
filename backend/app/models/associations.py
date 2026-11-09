from sqlalchemy import Column, DateTime, ForeignKey, Table

from app.db.database import Base
from app.models.base import now


user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("assigned_at", DateTime(timezone=True), default=now),
)

user_cleanrooms = Table(
    "user_cleanrooms",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("cleanroom_id", ForeignKey("cleanrooms.id", ondelete="CASCADE"), primary_key=True),
)