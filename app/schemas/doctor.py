from pydantic import BaseModel
from uuid import UUID
from datetime import datetime, time
from typing import Optional, List

class WorkingHoursInput(BaseModel):
    day_of_week: int  # 0=Mon, 6=Sun
    is_active: bool = True
    start_time: time
    end_time: time

class DoctorCreate(BaseModel):
    specialty: str
    bio: Optional[str] = None
    years_experience: int = 0
    qualification: Optional[str] = None
    practice_name: str
    address: str
    city: str
    province: str
    consultation_fee: float
    slot_duration_minutes: int = 20
    medical_aids: List[str] = []
    languages: List[str] = ["English"]
    working_hours: List[WorkingHoursInput] = []

class DoctorResponse(BaseModel):
    id: UUID
    user_id: UUID
    specialty: str
    bio: Optional[str]
    years_experience: int
    qualification: Optional[str]
    practice_name: str
    address: str
    city: str
    province: str
    consultation_fee: float
    slot_duration_minutes: int
    medical_aids: List[str]
    languages: List[str]
    is_active: bool
    is_verified: bool
    created_at: datetime

    class Config:
        from_attributes = True

class SlotResponse(BaseModel):
    id: UUID
    doctor_id: UUID
    date: str
    start_time: str
    end_time: str
    status: str

    class Config:
        from_attributes = True

class DoctorWithSlotsResponse(DoctorResponse):
    slots: List[SlotResponse] = []