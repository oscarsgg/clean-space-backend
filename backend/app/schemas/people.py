from pydantic import BaseModel, EmailStr, Field


class PersonIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)
    full_name: str = Field(min_length=2, max_length=150)
    role_codes: list[str] = Field(default_factory=lambda: ["PERSONAL_AUTORIZADO"])


class PersonUpdate(BaseModel):
    full_name: str | None = None
    status: str | None = None
    role_codes: list[str] | None = None