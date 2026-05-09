from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class RatingCreate(BaseModel):
    booking_id: str
    rating: int
    comment: Optional[str] = None


class RatingResponse(BaseModel):
    id: str
    patient_id: str
    doctor_id: str
    booking_id: str
    rating: int
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True