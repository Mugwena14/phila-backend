from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid

from app.db.database import Base


class Rating(Base):
    __tablename__ = "ratings"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    doctor_id  = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False, unique=True)
    rating     = Column(Integer, nullable=False)   # 1–5
    comment    = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())