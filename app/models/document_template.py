from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.db.database import Base

class DocumentTemplate(Base):
    __tablename__ = "document_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    placeholders = Column(Text, nullable=False)  # JSON array of placeholder keys
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    doctor = relationship("Doctor", foreign_keys=[doctor_id])