from sqlalchemy import Column, String, Boolean, DateTime, Integer, Float, ForeignKey, ARRAY
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.database import Base

class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)

    # Professional info
    specialty = Column(String, nullable=False)
    bio = Column(String, nullable=True)
    years_experience = Column(Integer, default=0)
    qualification = Column(String, nullable=True)

    # Location
    practice_name = Column(String, nullable=False)
    address = Column(String, nullable=False)
    city = Column(String, nullable=False)
    province = Column(String, nullable=False)
    latitude = Column(Float, nullable=True)   
    longitude = Column(Float, nullable=True)  

    # Settings
    consultation_fee = Column(Float, nullable=False, default=0.0)
    slot_duration_minutes = Column(Integer, default=20)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    rating = Column(Float, default=0.0)
    total_reviews = Column(Integer, default=0)

    # Medical aids accepted — stored as array of strings
    medical_aids = Column(ARRAY(String), default=[])

    # Languages spoken
    languages = Column(ARRAY(String), default=["English"])

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="doctor_profile")
    slots = relationship("Slot", back_populates="doctor", cascade="all, delete-orphan")