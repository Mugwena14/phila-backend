"""
Audit log for every document send attempt.

POPIA-relevant: if a patient ever disputes whether they received a document
or claims it went to the wrong number, this table is the record. Every send
attempt - successful or failed - lands here with the recipient and timestamp.
Never delete rows from this table; retention requirement is at minimum the
same as patient medical records (6 years for adults in SA).
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.database import Base


class DocumentSendLog(Base):
    __tablename__ = "document_send_log"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("patient_documents.id"), nullable=False, index=True)
    channel = Column(String(32), nullable=False)       # 'whatsapp' | 'email'
    recipient = Column(String(256), nullable=False)    # phone (+27...) or email
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    initiated_by = Column(UUID(as_uuid=True), nullable=True)  # users.id of the doctor/receptionist
