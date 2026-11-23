from pydantic import BaseModel, Field


class RoomIn(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=120)
    location: str = ""
    description: str = ""
    access_level: str = "STANDARD"
    status: str = "ACTIVE"
    capacity: int = Field(default=8, ge=1)
    iso_class: str = Field(default="ISO 7", min_length=1, max_length=20)


class RoomUpdate(BaseModel):
    name: str | None = None
    location: str | None = None
    description: str | None = None
    access_level: str | None = None
    status: str | None = None
    capacity: int | None = Field(default=None, ge=1)
    iso_class: str | None = Field(default=None, min_length=1, max_length=20)