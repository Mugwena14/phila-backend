from sqlalchemy import Column, String, DateTime, ForeignKey, Text
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
    # visit_summary | template_{uuid}
    content = Column(Text, nullable=False)  # JSON string
    generated_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Phase 3a - send tracking. Populated by POST /documents/{id}/send.
    # All three nullable; a doc that hasn't been sent on a channel just has the
    # column as NULL. Frontend reads these to render the Sent/Not-yet-sent pill.
    sent_via_whatsapp_at = Column(DateTime(timezone=True), nullable=True)
    sent_via_email_at = Column(DateTime(timezone=True), nullable=True)
    recalled_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("User", foreign_keys=[patient_id])
    doctor = relationship("Doctor", foreign_keys=[doctor_id])
