from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class RfidTagCreate(BaseModel):
    uid: str
    isActive: bool = True
    assigned_date: date = Field(default_factory=date.today)
    employee_id: int | None = None


class RfidTagUpdate(BaseModel):
    isActive: bool | None = None
    employee_id: int | None = None


class RfidTagRead(BaseModel):
    uid: str
    isActive: bool
    assigned_date: date
    employee_id: int | None = None

    model_config = ConfigDict(from_attributes=True)