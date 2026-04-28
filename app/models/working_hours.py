from sqlalchemy import Column, String, Boolean, Time, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.database import Base

class WorkingHours(Base):
    __tablename__ = "working_hours"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)

    # 0 = Monday, 6 = Sunday
    day_of_week = Column(Integer, nullable=False)
    is_active = Column(Boolean, default=True)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    # Relationships
    doctor = relationship("Doctor", backref="working_hours")