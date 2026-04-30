from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.database import Base

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    slot_id = Column(UUID(as_uuid=True), ForeignKey("slots.id"), nullable=False, unique=True)

    status = Column(String, default="confirmed")
    # confirmed | cancelled | completed | no_show

    reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    # No-show risk score 0-100
    risk_score = Column(String, default="0")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    patient = relationship("User", foreign_keys=[patient_id])
    doctor = relationship("Doctor", foreign_keys=[doctor_id])
    slot = relationship("Slot", foreign_keys=[slot_id])

    # none / low / high
    crisis_flag = Column(String, nullable=True)  

    # improving / not_improving / unclear
    outcome = Column(String, nullable=True)  