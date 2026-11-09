from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class EmployeeCreate(BaseModel):
    firstName: str
    lastName: str
    department: str
    isActive: bool = True
    created_at: date = Field(default_factory=date.today)


class EmployeeUpdate(BaseModel):
    firstName: str | None = None
    lastName: str | None = None
    department: str | None = None
    isActive: bool | None = None


class EmployeeRead(BaseModel):
    id: int
    firstName: str
    lastName: str
    department: str
    isActive: bool
    created_at: date

    model_config = ConfigDict(from_attributes=True)