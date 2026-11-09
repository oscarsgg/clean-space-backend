from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ParameterIn(BaseModel):
    metric: str
    min_value: float
    max_value: float
    unit: str
    enabled: bool = True


class ReadingIn(BaseModel):
    device_id: int
    cleanroom_id: int
    metric: str
    value: float
    unit: str
    recorded_at: datetime | None = None


class CommandIn(BaseModel):
    device_id: int
    command: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandAckIn(BaseModel):
    status: str = "ACKNOWLEDGED"