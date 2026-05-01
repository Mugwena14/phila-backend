from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.db.database import Base

class PatientMedication(Base):
    __tablename__ = "patient_medications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=True)

    medication_name = Column(String, nullable=False)
    detected_from_intake = Column(Boolean, default=True)
    last_prescribed_date = Column(String, nullable=True)
    estimated_refill_date = Column(String, nullable=True)
    refill_notified = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    patient = relationship("User", foreign_keys=[patient_id])