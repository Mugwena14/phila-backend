from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional, Any

class BookingCreate(BaseModel):
    slot_id: UUID
    reason: Optional[str] = None

class WalkInBookingCreate(BaseModel):
    patient_name: str
    patient_phone: str
    slot_id: Optional[UUID] = None
    reason: Optional[str] = None
    receptionist_note: Optional[str] = None

class BookingUpdate(BaseModel):
    reason: Optional[str] = None
    receptionist_note: Optional[str] = None
    status: Optional[str] = None

class BookingResponse(BaseModel):
    id: UUID
    patient_id: UUID
    doctor_id: UUID
    slot_id: UUID
    status: str
    reason: Optional[str] = None
    receptionist_note: Optional[str] = None
    risk_score: str
    crisis_flag: Optional[str] = None
    outcome: Optional[str] = None
    is_walk_in: Optional[bool] = False
    arrived_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
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