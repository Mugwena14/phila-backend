from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from typing import List
from datetime import datetime, date
from uuid import UUID

from app.db.database import get_db
from app.models.booking import Booking
from app.models.slot import Slot
from app.models.doctor import Doctor
from app.models.user import User
from app.models.waitlist import Waitlist
from app.schemas.booking import (
    BookingCreate,
    BookingResponse,
    BookingDetailResponse,
    WaitlistCreate,
    WaitlistResponse,
)
from app.services.risk_service import calculate_risk_score
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/bookings", tags=["bookings"])
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


@router.post("/", response_model=BookingResponse, status_code=201)
def create_booking(
    data: BookingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check slot exists and is available
    slot = db.query(Slot).filter(Slot.id == data.slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.status != "available":
        raise HTTPException(status_code=400, detail="Slot is no longer available")

    # Check patient doesn't already have a booking with this doctor on this date
    existing = (
        db.query(Booking)
        .join(Slot)
        .filter(
            Booking.patient_id == current_user.id,
            Booking.doctor_id == slot.doctor_id,
            Slot.date == slot.date,
            Booking.status == "confirmed",
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail="You already have a booking with this doctor on this date"
        )

    # Calculate days until appointment for risk scoring
    days_until = (slot.date - date.today()).days

    # Calculate risk score
    risk = calculate_risk_score(db, current_user.id, days_until)

    # Lock the slot
    slot.status = "booked"

    # Create the booking
    booking = Booking(
        patient_id=current_user.id,
        doctor_id=slot.doctor_id,
        slot_id=slot.id,
        reason=data.reason,
        risk_score=str(risk),
        status="confirmed",
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    return booking


@router.get("/my", response_model=List[BookingDetailResponse])
def get_my_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    bookings = (
        db.query(Booking)
        .filter(Booking.patient_id == current_user.id)
        .order_by(Booking.created_at.desc())
        .all()
    )

    result = []
    for b in bookings:
        slot = db.query(Slot).filter(Slot.id == b.slot_id).first()
        doctor = db.query(Doctor).filter(Doctor.id == b.doctor_id).first()
        result.append(
            BookingDetailResponse(
                id=b.id,
                patient_id=b.patient_id,
                doctor_id=b.doctor_id,
                slot_id=b.slot_id,
                status=b.status,
                reason=b.reason,
                risk_score=b.risk_score,
                created_at=b.created_at,
                slot_date=str(slot.date) if slot else None,
                slot_start_time=str(slot.start_time) if slot else None,
                slot_end_time=str(slot.end_time) if slot else None,
                practice_name=doctor.practice_name if doctor else None,
                specialty=doctor.specialty if doctor else None,
            )
        )
    return result


@router.get("/practice", response_model=List[BookingDetailResponse])
def get_practice_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get the doctor profile for this user
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    bookings = (
        db.query(Booking)
        .filter(
            Booking.doctor_id == doctor.id,
            Booking.status == "confirmed",
        )
        .order_by(Booking.created_at.desc())
        .all()
    )

    result = []
    for b in bookings:
        slot = db.query(Slot).filter(Slot.id == b.slot_id).first()
        patient = db.query(User).filter(User.id == b.patient_id).first()
        result.append(
            BookingDetailResponse(
                id=b.id,
                patient_id=b.patient_id,
                doctor_id=b.doctor_id,
                slot_id=b.slot_id,
                status=b.status,
                reason=b.reason,
                risk_score=b.risk_score,
                created_at=b.created_at,
                slot_date=str(slot.date) if slot else None,
                slot_start_time=str(slot.start_time) if slot else None,
                slot_end_time=str(slot.end_time) if slot else None,
                doctor_name=patient.full_name if patient else None,
                practice_name=doctor.practice_name,
                specialty=doctor.specialty,
            )
        )
    return result


@router.delete("/{booking_id}", status_code=200)
def cancel_booking(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if str(booking.patient_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your booking")

    if booking.status != "confirmed":
        raise HTTPException(status_code=400, detail="Booking already cancelled or completed")

    # Cancel the booking
    booking.status = "cancelled"

    # Release the slot back to available
    slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    if slot:
        slot.status = "available"

    db.commit()

    # Check waitlist — notify first person waiting
    waiting = (
        db.query(Waitlist)
        .filter(
            Waitlist.doctor_id == booking.doctor_id,
            Waitlist.date == slot.date if slot else None,
            Waitlist.status == "waiting",
        )
        .order_by(Waitlist.created_at.asc())
        .first()
    )

    if waiting:
        waiting.status = "notified"
        db.commit()
        # In Week 4 we fire a WhatsApp message here

    return {"message": "Booking cancelled successfully", "slot_released": True}


@router.post("/waitlist", response_model=WaitlistResponse, status_code=201)
def join_waitlist(
    data: WaitlistCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Check not already on waitlist
    existing = (
        db.query(Waitlist)
        .filter(
            Waitlist.patient_id == current_user.id,
            Waitlist.doctor_id == data.doctor_id,
            Waitlist.date == data.date,
            Waitlist.status == "waiting",
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already on waitlist for this date")

    entry = Waitlist(
        patient_id=current_user.id,
        doctor_id=data.doctor_id,
        date=data.date,
        status="waiting",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return WaitlistResponse(
        id=entry.id,
        patient_id=entry.patient_id,
        doctor_id=entry.doctor_id,
        date=str(entry.date),
        status=entry.status,
        created_at=entry.created_at,
    )