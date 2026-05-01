from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional, Any

class BookingCreate(BaseModel):
    slot_id: UUID
    reason: Optional[str] = None

class BookingResponse(BaseModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    slot_id: UUID
    status: str
    reason: Optional[str]
    risk_score: str
    created_at: datetime

    class Config:
        from_attributes = True

class BookingDetailResponse(BookingResponse):
    slot_date: Optional[str] = None
    slot_start_time: Optional[str] = None
    slot_end_time: Optional[str] = None
    doctor_name: Optional[str] = None
    practice_name: Optional[str] = None
    specialty: Optional[str] = None
    intake_status: Optional[str] = None
    intake_brief: Optional[Any] = None
    crisis_flag: Optional[str] = None

class WaitlistCreate(BaseModel):
    doctor_id: UUID
    date: str

class WaitlistResponse(BaseModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    date: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True