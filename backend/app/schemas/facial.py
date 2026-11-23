from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class FacialEnrollRequest(BaseModel):
    employee_id: int
    image_base64: str = Field(description="Base64 encoded JPG/PNG image from camera")
    sample_type: str = Field(default="full_face", description="'full_face', 'masked_cleanroom', 'eyes_only'")
    notes: Optional[str] = None


class FacialEnrollResponse(BaseModel):
    success: bool
    biometric_id: int
    employee_id: int
    sample_type: str
    is_masked: bool
    detection_confidence: float
    message: str


class FacialVerifyRequest(BaseModel):
    image_base64: str = Field(description="Base64 encoded JPG/PNG image from camera")
    door_id: str = Field(default="entrada-principal", description="Cleanroom door identifier")
    cleanroom_id: Optional[int] = Field(default=None, description="Optional cleanroom identifier")


class FacialVerifyResponse(BaseModel):
    authorized: bool
    reason: str
    employee_id: Optional[int] = None
    employee_name: Optional[str] = None
    department: Optional[str] = None
    confidence_score: float = 0.0
    match_mode: str = "NONE"  # "FULL_FACE", "PERIOCULAR_EYES", "NONE"
    is_masked: bool = False
    low_light_enhanced: bool = False
    door_id: str
    door_unlocked: bool = False
    event_id: Optional[int] = None


class FacialBiometricRead(BaseModel):
    id: int
    employee_id: int
    sample_type: str
    is_masked: bool = False
    is_active: bool
    created_at: datetime
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
