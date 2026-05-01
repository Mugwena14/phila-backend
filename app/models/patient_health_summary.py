from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.db.database import Base

class PatientHealthSummary(Base):
    __tablename__ = "patient_health_summaries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)

    total_visits = Column(String, default="0")
    last_visit_date = Column(String, nullable=True)
    specialties_seen = Column(Text, nullable=True)   # JSON array
    medications_detected = Column(Text, nullable=True)  # JSON array
    care_gaps_detected = Column(Text, nullable=True)    # JSON array
    last_scanned_at = Column(DateTime(timezone=True), nullable=True)

    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    patient = relationship("User", foreign_keys=[patient_id])