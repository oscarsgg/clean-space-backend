from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DoorPermissionCreate(BaseModel):
    rfid_uid: str = Field(min_length=1, max_length=40)
    door_id: str = Field(min_length=1, max_length=100)
    is_active: bool = True


class DoorPermissionUpdate(BaseModel):
    is_active: bool | None = None


class DoorPermissionRead(BaseModel):
    id: int
    rfid_uid: str
    door_id: str
    is_active: bool
    created_at: date

    model_config = ConfigDict(from_attributes=True)


class DeviceAccessRequest(BaseModel):
    uid: str = Field(min_length=1, max_length=40)
    door_id: str = Field(min_length=1, max_length=100)


class DeviceAccessResponse(BaseModel):
    authorized: bool
    reason: str
    uid: str
    door_id: str


class DeviceSyncResponse(BaseModel):
    door_id: str
    allowed_uids: list[str]


class OfflineAccessEvent(BaseModel):
    uid: str = Field(min_length=1, max_length=40)
    door_id: str = Field(min_length=1, max_length=100)
    access_status: Literal["offline_allowed", "denied"]
    created_at: date


class OfflineEventsRequest(BaseModel):
    events: list[OfflineAccessEvent] = Field(min_length=1, max_length=100)