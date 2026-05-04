from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.doctor import Doctor

router = APIRouter()


# ─── Haversine distance ───────────────────────────────────────────────────────

def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ─── Travel time estimate ─────────────────────────────────────────────────────

def estimate_travel_minutes(distance_km: float) -> int:
    return max(1, round((distance_km / 30) * 60 + 3))


# ─── Availability from slots ──────────────────────────────────────────────────

def get_availability(slots: list) -> tuple[str, Optional[str]]:
    now = datetime.now(timezone.utc)
    today_end = now.replace(hour=23, minute=59, second=59)
    week_end = now + timedelta(days=7)

    def slot_datetime(s) -> datetime:
        return datetime.combine(s.date, s.start_time).replace(tzinfo=timezone.utc)

    open_slots = sorted(
        [s for s in slots if slot_datetime(s) > now and s.status == "available"],
        key=lambda s: slot_datetime(s),
    )

    if not open_slots:
        return "unavailable", None

    next_slot = open_slots[0]
    next_dt = slot_datetime(next_slot)

    # Windows-safe date format (no %-d)
    next_label = f"{next_dt.day} {next_dt.strftime('%b')} · {next_dt.strftime('%H:%M')}"

    if next_dt <= today_end:
        return "available_today", next_label
    elif next_dt <= week_end:
        return "available_week", next_label
    else:
        return "unavailable", next_label


# ─── Response schema ──────────────────────────────────────────────────────────

class NearbyDoctorResponse(BaseModel):
    id: str
    practice_name: str
    specialty: str
    city: str
    consultation_fee: float
    latitude: float
    longitude: float
    availability_status: str
    next_available_slot: Optional[str]
    travel_time_minutes: int
    medical_aids: List[str]
    years_experience: int
    distance_km: float

    class Config:
        from_attributes = True


# ─── Endpoint ─────────────────────────────────────────────────────────────────

@router.get("/doctors/nearby", response_model=List[NearbyDoctorResponse])
def get_nearby_doctors(
    lat: float = Query(..., description="Patient latitude"),
    lng: float = Query(..., description="Patient longitude"),
    radius_km: float = Query(20.0, description="Search radius in km"),
    db: Session = Depends(get_db),
):
    all_doctors = (
        db.query(Doctor)
        .filter(
            Doctor.is_active == True,
            Doctor.latitude.isnot(None),
            Doctor.longitude.isnot(None),
        )
        .all()
    )

    results = []

    for doctor in all_doctors:
        distance = haversine_km(lat, lng, doctor.latitude, doctor.longitude)
        if distance > radius_km:
            continue

        status, next_slot = get_availability(doctor.slots)

        results.append(NearbyDoctorResponse(
            id=str(doctor.id),
            practice_name=doctor.practice_name,
            specialty=doctor.specialty,
            city=doctor.city,
            consultation_fee=doctor.consultation_fee,
            latitude=doctor.latitude,
            longitude=doctor.longitude,
            availability_status=status,
            next_available_slot=next_slot,
            travel_time_minutes=estimate_travel_minutes(distance),
            medical_aids=doctor.medical_aids or [],
            years_experience=doctor.years_experience,
            distance_km=round(distance, 2),
        ))

    results.sort(key=lambda d: d.distance_km)
    return results