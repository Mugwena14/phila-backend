from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from app.db.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    type = Column(String, nullable=False)
    # booking_confirmed | new_booking | booking_cancelled | appointment_reminder
    # patient_checkin | no_show | slots_low | daily_summary | document_ready
    # triage_summary | new_doctor_nearby

    title = Column(String, nullable=False)
    body = Column(String, nullable=False)
    is_read = Column(Boolean, default=False)

    # Where to navigate when tapped
    action_type = Column(String, nullable=True)
    action_data = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())