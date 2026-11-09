from datetime import datetime

from pydantic import BaseModel


class PermissionIn(BaseModel):
    user_id: int
    cleanroom_id: int
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool = True


class PermissionUpdate(BaseModel):
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool | None = None


class AccessValidationIn(BaseModel):
    device_id: int
    uid: str
    direction: str = "ENTRY"