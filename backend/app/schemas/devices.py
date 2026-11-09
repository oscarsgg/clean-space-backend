from datetime import datetime

from pydantic import BaseModel, Field


class DeviceIn(BaseModel):
    cleanroom_id: int
    code: str
    type: str
    serial_number: str


class DeviceUpdate(BaseModel):
    cleanroom_id: int | None = None
    type: str | None = None
    status: str | None = None
    firmware_version: str | None = None


class CardIn(BaseModel):
    user_id: int
    uid: str = Field(min_length=4, max_length=64)
    label: str = ""
    expires_at: datetime | None = None


class CardUpdate(BaseModel):
    status: str | None = None
    label: str | None = None
    expires_at: datetime | None = None