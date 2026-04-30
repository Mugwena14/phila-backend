from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.database import Base

class IntakeBrief(Base):
    __tablename__ = "intake_briefs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False, unique=True)

    main_concern = Column(Text, nullable=True)
    duration = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    medications = Column(Text, nullable=True)   # JSON array as string
    allergies = Column(Text, nullable=True)     # JSON array as string
    additional_notes = Column(Text, nullable=True)
    language_used = Column(String, default="English")
    crisis_flagged = Column(Boolean, default=False)
    raw_brief = Column(Text, nullable=True)     # Full JSON from Claude

    created_at = Column(DateTime(timezone=True), server_default=func.now())