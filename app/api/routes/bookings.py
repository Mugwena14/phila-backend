from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, date, timedelta
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
from app.services.whatsapp import send_whatsapp_message
from app.tasks.whatsapp_tasks import (
    send_intake_whatsapp,
    send_appointment_reminder,
    send_followup_whatsapp,
    check_intake_completion,
)
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
import logging

router = APIRouter(prefix="/bookings", tags=["bookings"])
security = HTTPBearer()
logger = logging.getLogger(__name__)


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
    slot = db.query(Slot).filter(Slot.id == data.slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.status != "available":
        raise HTTPException(status_code=400, detail="Slot is no longer available")

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

    days_until = (slot.date - date.today()).days
    risk = calculate_risk_score(db, current_user.id, days_until)

    slot.status = "booked"

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

    # ── WEEK 4 AGENT TASKS ──────────────────────────────────────────

    booking_id = str(booking.id)
    appt_datetime = datetime.combine(slot.date, slot.start_time)

    # 1. Intake agent — fires 10 seconds after booking
    send_intake_whatsapp.apply_async(
        args=[booking_id],
        countdown=10,
    )
    logger.info(f"Intake task queued for booking {booking_id}")

    # 2. Intake completion check — fires 30 minutes later
    # If patient hasn't completed intake, bumps risk score +20
    check_intake_completion.apply_async(
        args=[booking_id],
        countdown=1800,
    )
    logger.info(f"Intake completion check queued for booking {booking_id}")

    # 3. No-show prevention — reminder ladder based on risk score

    # 24hr reminder — every patient
    remind_24hr = appt_datetime - timedelta(hours=24)
    if remind_24hr > datetime.now():
        send_appointment_reminder.apply_async(
            args=[booking_id, 24],
            eta=remind_24hr,
        )
        logger.info(f"24hr reminder queued for booking {booking_id}")

    # 2hr reminder — every patient
    remind_2hr = appt_datetime - timedelta(hours=2)
    if remind_2hr > datetime.now():
        send_appointment_reminder.apply_async(
            args=[booking_id, 2],
            eta=remind_2hr,
        )
        logger.info(f"2hr reminder queued for booking {booking_id}")

    # 48hr reminder — medium and high risk (score >= 30)
    if risk >= 30:
        remind_48hr = appt_datetime - timedelta(hours=48)
        if remind_48hr > datetime.now():
            send_appointment_reminder.apply_async(
                args=[booking_id, 48],
                eta=remind_48hr,
            )
            logger.info(f"48hr reminder queued for booking {booking_id} (risk: {risk})")

    # 72hr reminder — high risk only (score >= 65)
    if risk >= 65:
        remind_72hr = appt_datetime - timedelta(hours=72)
        if remind_72hr > datetime.now():
            send_appointment_reminder.apply_async(
                args=[booking_id, 72],
                eta=remind_72hr,
            )
            logger.info(f"72hr reminder queued for booking {booking_id} (risk: {risk})")

    # 4. Follow-up agent — fires 3 days after appointment
    followup_time = appt_datetime + timedelta(days=3)
    send_followup_whatsapp.apply_async(
        args=[booking_id],
        eta=followup_time,
    )
    logger.info(f"Follow-up task queued for booking {booking_id} at {followup_time}")

    # ────────────────────────────────────────────────────────────────

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

        # Check intake status
        brief = db.execute(
            text("SELECT id FROM intake_briefs WHERE booking_id = :id"),
            {"id": str(b.id)}
        ).fetchone()
        intake_status = "complete" if brief else "pending"

        result.append(
            BookingDetailResponse(
                id=b.id,
                patient_id=b.patient_id,
                doctor_id=b.doctor_id,
                slot_id=b.slot_id,
                status=b.status,
                reason=b.reason,
                risk_score=b.risk_score,
                crisis_flag=getattr(b, 'crisis_flag', None),
                intake_status=intake_status,
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

        # Check intake status + fetch brief
        brief = db.execute(
            text("""
                SELECT main_concern, duration, severity, medications,
                       allergies, additional_notes, crisis_flagged, language_used
                FROM intake_briefs
                WHERE booking_id = :id
            """),
            {"id": str(b.id)}
        ).fetchone()

        intake_status = "complete" if brief else "pending"
        intake_brief = dict(brief._mapping) if brief else None

        # Parse JSON strings back to lists
        if intake_brief:
            import json
            try:
                intake_brief["medications"] = json.loads(
                    intake_brief.get("medications", "[]")
                )
            except Exception:
                intake_brief["medications"] = []
            try:
                intake_brief["allergies"] = json.loads(
                    intake_brief.get("allergies", "[]")
                )
            except Exception:
                intake_brief["allergies"] = []

        result.append(
            BookingDetailResponse(
                id=b.id,
                patient_id=b.patient_id,
                doctor_id=b.doctor_id,
                slot_id=b.slot_id,
                status=b.status,
                reason=b.reason,
                risk_score=b.risk_score,
                crisis_flag=getattr(b, 'crisis_flag', None),
                intake_status=intake_status,
                intake_brief=intake_brief,
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

    booking.status = "cancelled"

    slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    if slot:
        slot.status = "available"

    db.commit()

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

        waiting_user = db.query(User).filter(
            User.id == waiting.patient_id
        ).first()
        if waiting_user and slot:
            send_whatsapp_message(
                waiting_user.phone,
                f"Good news! A slot just opened up on {slot.date}. "
                f"Open Phila to book it before it's gone! 🎉"
            )
            logger.info(
                f"Waitlist notification sent to {waiting_user.phone} "
                f"for slot on {slot.date}"
            )

    return {"message": "Booking cancelled successfully", "slot_released": True}


@router.post("/waitlist", response_model=WaitlistResponse, status_code=201)
def join_waitlist(
    data: WaitlistCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
        raise HTTPException(
            status_code=400,
            detail="Already on waitlist for this date"
        )

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