from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
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
    # confirmed | arrived | in_consultation | completed | cancelled | no_show

    reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    receptionist_note = Column(Text, nullable=True)

    # Risk + AI fields
    risk_score = Column(String, default="0")
    crisis_flag = Column(String, nullable=True)
    outcome = Column(String, nullable=True)

    # Walk-in flag
    is_walk_in = Column(Boolean, default=False)

    # Timestamps
    arrived_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    patient = relationship("User", foreign_keys=[patient_id])
    doctor = relationship("Doctor", foreign_keys=[doctor_id])
    slot = relationship("Slot", foreign_keys=[slot_id])