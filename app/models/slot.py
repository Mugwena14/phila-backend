from sqlalchemy import Column, String, DateTime, Date, Time, ForeignKey, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.database import Base

class Slot(Base):
    __tablename__ = "slots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    date = Column(Date, nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    status = Column(String, default="available")
    # available | booked | blocked

    # Blocking fields
    blocked_reason = Column(String, nullable=True)
    blocked_by = Column(UUID(as_uuid=True), nullable=True)

    doctor = relationship("Doctor", foreign_keys=[doctor_id])