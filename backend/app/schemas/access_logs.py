from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class AccessLogCreate(BaseModel):
    rfid_uid: str
    render_id: str
    access_status: str
    created_at: date = Field(default_factory=date.today)


class AccessLogRead(BaseModel):
    id: int
    rfid_uid: str
    render_id: str
    access_status: str
    created_at: date

    model_config = ConfigDict(from_attributes=True)