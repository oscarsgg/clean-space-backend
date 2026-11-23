from datetime import datetime
from typing import Literal

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


class AccessSimulationIn(BaseModel):
    cleanroom_id: int
    device_id: int | None = None
    uid: str
    direction: Literal["ENTRY", "EXIT"] = "ENTRY"