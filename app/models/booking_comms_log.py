"""
Audit log for booking-related comms (walk-in welcome WhatsApp, future
appointment reminders, etc).

Mirrors the document_send_log pattern. Why a separate table from
document_send_log: different domain (booking lifecycle vs document lifecycle),
different retention class under POPIA (booking comms = appointment record,
document comms = clinical record), different query patterns (most lookups
will be "what comms went out for this booking" not "for this document").

Why a separate table from notifications: notifications is the patient-facing
in-app feed (document_ready, appointment_reminder, etc). This table is the
server-side audit log of OUTBOUND messages we triggered, regardless of channel.
Mixing them would confuse the patient feed with practice operations.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.database import Base


class BookingCommsLog(Base):
    __tablename__ = "booking_comms_log"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False, index=True)

    # walkin_welcome | reminder | reschedule_notice | cancellation_notice
    comms_type = Column(String(64), nullable=False)

    channel = Column(String(32), nullable=False)        # 'whatsapp' | 'email' | 'sms'
    recipient = Column(String(256), nullable=False)     # phone or email
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    initiated_by = Column(UUID(as_uuid=True), nullable=True)  # receptionist/doctor user_id