from sqlalchemy import Column, String, Float, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from app.db.database import Base

class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)

    height_cm = Column(Float, nullable=True)
    weight_kg = Column(Float, nullable=True)
    blood_type = Column(String, nullable=True)
    date_of_birth = Column(String, nullable=True)

    user = relationship("User", backref="patient_profile")