from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date, datetime, timedelta
from uuid import UUID

from app.db.database import get_db
from app.models.doctor import Doctor
from app.models.slot import Slot
from app.models.working_hours import WorkingHours
from app.models.user import User
from app.schemas.doctor import (
    DoctorCreate,
    DoctorResponse,
    DoctorWithSlotsResponse,
    SlotResponse,
)
from app.services.slot_service import generate_slots_for_week
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/doctors", tags=["doctors"])
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/register", response_model=DoctorResponse, status_code=201)
def register_doctor(
    data: DoctorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check if doctor profile already exists
    existing = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Doctor profile already exists")

    doctor = Doctor(
        user_id=current_user.id,
        specialty=data.specialty,
        bio=data.bio,
        years_experience=data.years_experience,
        qualification=data.qualification,
        practice_name=data.practice_name,
        address=data.address,
        city=data.city,
        province=data.province,
        consultation_fee=data.consultation_fee,
        slot_duration_minutes=data.slot_duration_minutes,
        medical_aids=data.medical_aids,
        languages=data.languages,
    )
    db.add(doctor)
    db.flush()  # Get the doctor ID before commit

    # Save working hours
    for wh in data.working_hours:
        working_hours = WorkingHours(
            doctor_id=doctor.id,
            day_of_week=wh.day_of_week,
            is_active=wh.is_active,
            start_time=wh.start_time,
            end_time=wh.end_time,
        )
        db.add(working_hours)

    db.commit()
    db.refresh(doctor)

    # Auto-generate slots for the next 7 days
    generate_slots_for_week(db, doctor, date.today())

    return doctor


@router.get("/", response_model=List[DoctorResponse])
def search_doctors(
    specialty: Optional[str] = None,
    city: Optional[str] = None,
    medical_aid: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Doctor).filter(Doctor.is_active == True)

    if specialty:
        query = query.filter(Doctor.specialty.ilike(f"%{specialty}%"))
    if city:
        query = query.filter(Doctor.city.ilike(f"%{city}%"))
    if medical_aid:
        query = query.filter(Doctor.medical_aids.contains([medical_aid]))

    return query.all()


@router.get("/today", response_model=DoctorResponse)
def get_today_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")
    return doctor


@router.get("/{doctor_id}", response_model=DoctorResponse)
def get_doctor(doctor_id: UUID, db: Session = Depends(get_db)):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


@router.get("/{doctor_id}/slots", response_model=List[SlotResponse])
def get_doctor_slots(
    doctor_id: UUID,
    date: Optional[date] = None,
    db: Session = Depends(get_db),
):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    # If no date provided, use today
    target_date = date if date else datetime.today().date()

    # Auto-generate slots if none exist for this date
    from app.services.slot_service import generate_slots_for_day
    generate_slots_for_day(db, doctor, target_date)

    slots = (
        db.query(Slot)
        .filter(
            Slot.doctor_id == doctor_id,
            Slot.date == target_date,
            Slot.status == "available",
        )
        .order_by(Slot.start_time)
        .all()
    )

    # Serialize manually since date/time need string conversion
    return [
        SlotResponse(
            id=s.id,
            doctor_id=s.doctor_id,
            date=str(s.date),
            start_time=str(s.start_time),
            end_time=str(s.end_time),
            status=s.status,
        )
        for s in slots
    ]