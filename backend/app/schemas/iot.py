from pydantic import BaseModel, Field

from app.schemas.monitoring import ReadingIn


class TelemetryBatch(BaseModel):
    device_id: int
    readings: list[ReadingIn] = Field(min_length=1, max_length=100)


class DeviceAuthIn(BaseModel):
    code: str
    api_key: str


class HeartbeatIn(BaseModel):
    firmware_version: str | None = None
    rssi: int | None = None
    free_memory: int | None = None