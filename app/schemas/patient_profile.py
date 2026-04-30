from pydantic import BaseModel
from uuid import UUID
from typing import Optional

class PatientProfileUpdate(BaseModel):
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    blood_type: Optional[str] = None
    date_of_birth: Optional[str] = None

class PatientProfileResponse(BaseModel):
    id: UUID
    user_id: UUID
    height_cm: Optional[float]
    weight_kg: Optional[float]
    blood_type: Optional[str]
    date_of_birth: Optional[str]

    class Config:
        from_attributes = True