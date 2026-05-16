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
from app.utils.geocoding import geocode_address
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import File, UploadFile

import cloudinary
import cloudinary.uploader
from app.core.config import settings as app_settings

# Init Cloudinary
cloudinary.config(
    cloud_name=app_settings.CLOUDINARY_CLOUD_NAME,
    api_key=app_settings.CLOUDINARY_API_KEY,
    api_secret=app_settings.CLOUDINARY_API_SECRET,
)

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
async def register_doctor(
    data: DoctorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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

    coords = await geocode_address(data.address, data.city, data.province)
    if coords:
        doctor.latitude, doctor.longitude = coords

    db.add(doctor)
    db.flush()

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

    from app.services.slot_service import generate_slots_for_day
    for i in range(14):
        target = date.today() + timedelta(days=i)
        generate_slots_for_day(db, doctor, target)

    return doctor


@router.get("/search", response_model=List[DoctorResponse])
def general_search(
    q: Optional[str] = None,
    city: Optional[str] = None,
    medical_aid: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from sqlalchemy import or_
    query = db.query(Doctor).filter(Doctor.is_active == True)

    if q:
        query = query.filter(
            or_(
                Doctor.specialty.ilike(f"%{q}%"),
                Doctor.practice_name.ilike(f"%{q}%"),
                Doctor.city.ilike(f"%{q}%"),
                Doctor.bio.ilike(f"%{q}%"),
            )
        )
    if city:
        query = query.filter(Doctor.city.ilike(f"%{city}%"))
    if medical_aid:
        query = query.filter(Doctor.medical_aids.contains([medical_aid]))

    return query.all()


@router.get("/", response_model=List[DoctorResponse])
def search_doctors(
    specialty: Optional[str] = None,
    city: Optional[str] = None,
    medical_aid: Optional[str] = None,
    name: Optional[str] = None,
    db: Session = Depends(get_db),
):
    query = db.query(Doctor).filter(Doctor.is_active == True)

    if specialty:
        query = query.filter(Doctor.specialty.ilike(f"%{specialty}%"))
    if city:
        query = query.filter(Doctor.city.ilike(f"%{city}%"))
    if medical_aid:
        query = query.filter(Doctor.medical_aids.contains([medical_aid]))
    if name:
        query = query.filter(Doctor.practice_name.ilike(f"%{name}%"))

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
    dashboard: Optional[bool] = False,
    db: Session = Depends(get_db),
):
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    target_date = date if date else datetime.today().date()

    from app.services.slot_service import generate_slots_for_day
    generate_slots_for_day(db, doctor, target_date)

    query = db.query(Slot).filter(
        Slot.doctor_id == doctor_id,
        Slot.date == target_date,
    )

    if not dashboard:
        query = query.filter(Slot.status == "available")

    slots = query.order_by(Slot.start_time).all()

    return [
        SlotResponse(
            id=s.id,
            doctor_id=s.doctor_id,
            date=str(s.date),
            start_time=str(s.start_time),
            end_time=str(s.end_time),
            status=s.status,
            blocked_reason=getattr(s, 'blocked_reason', None),
        )
        for s in slots
    ]


@router.post("/upload-image")
async def upload_practice_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    current_images = doctor.practice_images or []
    if len(current_images) >= 4:
        raise HTTPException(status_code=400, detail="Maximum 4 images allowed")

    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=400, detail="Only JPEG, PNG and WebP images are allowed")

    try:
        result = cloudinary.uploader.upload(
            file.file,
            folder=f"phila/practices/{doctor.id}",
            transformation=[
                {"width": 1200, "height": 800, "crop": "fill", "quality": "auto", "fetch_format": "auto"}
            ]
        )
        url = result["secure_url"]
        public_id = result["public_id"]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")

    from sqlalchemy import text
    db.execute(
        text("UPDATE doctors SET practice_images = array_append(COALESCE(practice_images, ARRAY[]::text[]), :url) WHERE id = :id"),
        {"url": url, "id": str(doctor.id)}
    )
    db.commit()
    db.refresh(doctor)

    return {"url": url, "public_id": public_id, "practice_images": doctor.practice_images}


@router.delete("/remove-image")
async def remove_practice_image(
    image_url: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    try:
        parts = image_url.split("/upload/")
        if len(parts) == 2:
            public_id = parts[1].rsplit(".", 1)[0]
            cloudinary.uploader.destroy(public_id)
    except Exception:
        pass

    from sqlalchemy import text
    db.execute(
        text("UPDATE doctors SET practice_images = array_remove(practice_images, :url) WHERE id = :id"),
        {"url": image_url, "id": str(doctor.id)}
    )
    db.commit()
    db.refresh(doctor)

    return {"success": True, "practice_images": doctor.practice_images}