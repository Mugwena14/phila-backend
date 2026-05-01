from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.db.database import Base

class PatientDocument(Base):
    __tablename__ = "patient_documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=True)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)

    doc_type = Column(String, nullable=False)
    # sick_letter | medical_certificate | referral_letter
    # prescription_summary | visit_summary

    content = Column(Text, nullable=False)  # JSON string
    generated_by = Column(UUID(as_uuid=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    patient = relationship("User", foreign_keys=[patient_id])
    doctor = relationship("Doctor", foreign_keys=[doctor_id])