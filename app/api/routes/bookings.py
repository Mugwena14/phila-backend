from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
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
    BookingUpdate,
    WalkInBookingCreate,
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
from app.tasks.notification_tasks import (
    notify_booking_confirmed,
    notify_booking_cancelled,
    notify_patient_checkin,
)
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import text
import logging
import random
import string
import json

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
        is_walk_in=False,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    booking_id = str(booking.id)
    appt_datetime = datetime.combine(slot.date, slot.start_time)

    # 1. Intake agent — fires 10 seconds after booking
    send_intake_whatsapp.apply_async(args=[booking_id], countdown=10)
    logger.info(f"Intake task queued for booking {booking_id}")

    # 2. Intake completion check — fires 30 minutes later
    check_intake_completion.apply_async(args=[booking_id], countdown=1800)
    logger.info(f"Intake completion check queued for booking {booking_id}")

    # 3. No-show prevention — reminder ladder based on risk score
    remind_24hr = appt_datetime - timedelta(hours=24)
    if remind_24hr > datetime.now():
        send_appointment_reminder.apply_async(args=[booking_id, 24], eta=remind_24hr)
        logger.info(f"24hr reminder queued for booking {booking_id}")

    remind_2hr = appt_datetime - timedelta(hours=2)
    if remind_2hr > datetime.now():
        send_appointment_reminder.apply_async(args=[booking_id, 2], eta=remind_2hr)
        logger.info(f"2hr reminder queued for booking {booking_id}")

    if risk >= 30:
        remind_48hr = appt_datetime - timedelta(hours=48)
        if remind_48hr > datetime.now():
            send_appointment_reminder.apply_async(args=[booking_id, 48], eta=remind_48hr)
            logger.info(f"48hr reminder queued for booking {booking_id} (risk: {risk})")

    if risk >= 65:
        remind_72hr = appt_datetime - timedelta(hours=72)
        if remind_72hr > datetime.now():
            send_appointment_reminder.apply_async(args=[booking_id, 72], eta=remind_72hr)
            logger.info(f"72hr reminder queued for booking {booking_id} (risk: {risk})")

    # 4. Follow-up agent — fires 3 days after appointment
    followup_time = appt_datetime + timedelta(days=3)
    send_followup_whatsapp.apply_async(args=[booking_id], eta=followup_time)
    logger.info(f"Follow-up task queued for booking {booking_id} at {followup_time}")

    # 5. Notify patient and doctor of confirmed booking
    notify_booking_confirmed(booking_id, db)
    logger.info(f"Booking confirmed notification sent for {booking_id}")

    return booking


@router.post("/walk-in", response_model=BookingDetailResponse, status_code=201)
def create_walk_in_booking(
    data: WalkInBookingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    patient = db.query(User).filter(
        User.phone == data.patient_phone,
        User.is_walk_in == False,
    ).first()

    if not patient:
        walkin_phone = f"WALKIN_{data.patient_phone}"
        patient = db.query(User).filter(User.phone == walkin_phone).first()

    if not patient:
        claim_code = "PHILA-" + "".join(
            random.choices(string.ascii_uppercase + string.digits, k=4)
        )
        walkin_phone = f"WALKIN_{data.patient_phone}"
        patient = User(
            full_name=data.patient_name,
            email=f"walkin_{claim_code.lower()}@phila.local",
            phone=walkin_phone,
            role="patient",
            is_walk_in=True,
            claim_code=claim_code,
            claimed=False,
            hashed_password="WALKIN_NO_PASSWORD",
        )
        db.add(patient)
        db.flush()
        logger.info(f"Walk-in patient created: {patient.full_name} — claim code: {claim_code}")

    slot = None
    if data.slot_id:
        slot = db.query(Slot).filter(Slot.id == data.slot_id).first()
        if not slot:
            raise HTTPException(status_code=404, detail="Slot not found")
        if slot.status == "booked":
            raise HTTPException(status_code=400, detail="Slot is already booked")
        slot.status = "booked"

    booking = Booking(
        patient_id=patient.id,
        doctor_id=doctor.id,
        slot_id=slot.id if slot else None,
        reason=data.reason,
        receptionist_note=data.receptionist_note,
        risk_score="20",
        status="confirmed",
        is_walk_in=True,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    try:
        notify_booking_confirmed(str(booking.id), db)
    except Exception as e:
        logger.warning(f"Walk-in notification failed (non-fatal): {e}")
    logger.info(f"Walk-in booking created: {booking.id} for {patient.full_name}")

    return BookingDetailResponse(
        id=booking.id,
        patient_id=booking.patient_id,
        doctor_id=booking.doctor_id,
        slot_id=booking.slot_id,
        status=booking.status,
        reason=booking.reason,
        receptionist_note=booking.receptionist_note,
        risk_score=booking.risk_score,
        is_walk_in=True,
        created_at=booking.created_at,
        slot_date=str(slot.date) if slot else None,
        slot_start_time=str(slot.start_time) if slot else None,
        slot_end_time=str(slot.end_time) if slot else None,
        slot_duration_minutes=doctor.slot_duration_minutes,
        practice_name=doctor.practice_name,
        specialty=doctor.specialty,
        latitude=doctor.latitude,
        longitude=doctor.longitude,
        address=doctor.address,
        city=doctor.city if doctor else None,
        province=doctor.province if doctor else None,
    )


@router.patch("/{booking_id}/status", status_code=200)
def update_booking_status(
    booking_id: UUID,
    data: BookingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if data.status:
        booking.status = data.status

        if data.status == "arrived":
            booking.arrived_at = datetime.now()
            notify_patient_checkin(str(booking_id), db)
            logger.info(f"Booking {booking_id} — patient arrived")

            elif data.status == "completed":
                booking.completed_at = datetime.now()
                # Release the slot so it can be reused for walk-ins
                slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
                if slot:
                    slot.status = "available"
                from app.tasks.whatsapp_tasks import send_rating_request_whatsapp
                send_rating_request_whatsapp.apply_async(
                    args=[str(booking_id)],
                    countdown=7200,
                )
                logger.info(f"Booking {booking_id} — consultation completed, slot released")

        elif data.status == "no_show":
            current = int(booking.risk_score or "0")
            booking.risk_score = str(min(current + 25, 100))
            slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
            if slot:
                slot.status = "available"
            logger.info(f"Booking {booking_id} — no-show. Risk bumped to {booking.risk_score}")

    if data.reason is not None:
        booking.reason = data.reason
    if data.receptionist_note is not None:
        booking.receptionist_note = data.receptionist_note

    db.commit()
    return {"message": "Booking updated", "status": booking.status}


@router.patch("/{booking_id}/move", status_code=200)
def move_booking(
    booking_id: UUID,
    new_slot_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    new_slot = db.query(Slot).filter(Slot.id == new_slot_id).first()
    if not new_slot or new_slot.status != "available":
        raise HTTPException(status_code=400, detail="New slot not available")

    old_slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    if old_slot:
        old_slot.status = "available"

    new_slot.status = "booked"
    booking.slot_id = new_slot.id
    db.commit()

    logger.info(f"Booking {booking_id} moved to slot {new_slot_id}")

    return {
        "message": "Booking moved successfully",
        "new_date": str(new_slot.date),
        "new_time": str(new_slot.start_time),
    }


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
                is_walk_in=getattr(b, 'is_walk_in', False),
                created_at=b.created_at,
                slot_date=str(slot.date) if slot else None,
                slot_start_time=str(slot.start_time) if slot else None,
                slot_end_time=str(slot.end_time) if slot else None,
                slot_duration_minutes=doctor.slot_duration_minutes if doctor else None,
                practice_name=doctor.practice_name if doctor else None,
                specialty=doctor.specialty if doctor else None,
                latitude=doctor.latitude if doctor else None,
                longitude=doctor.longitude if doctor else None,
                address=doctor.address if doctor else None,
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
        .filter(Booking.doctor_id == doctor.id)
        .order_by(Booking.created_at.desc())
        .all()
    )

    result = []
    for b in bookings:
        slot = db.query(Slot).filter(Slot.id == b.slot_id).first()
        patient = db.query(User).filter(User.id == b.patient_id).first()

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

        if intake_brief:
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
                receptionist_note=getattr(b, 'receptionist_note', None),
                risk_score=b.risk_score,
                crisis_flag=getattr(b, 'crisis_flag', None),
                intake_status=intake_status,
                intake_brief=intake_brief,
                is_walk_in=getattr(b, 'is_walk_in', False),
                arrived_at=getattr(b, 'arrived_at', None),
                completed_at=getattr(b, 'completed_at', None),
                created_at=b.created_at,
                slot_date=str(slot.date) if slot else None,
                slot_start_time=str(slot.start_time) if slot else None,
                slot_end_time=str(slot.end_time) if slot else None,
                slot_duration_minutes=doctor.slot_duration_minutes,
                doctor_name=patient.full_name if patient else None,
                practice_name=doctor.practice_name,
                specialty=doctor.specialty,
                latitude=doctor.latitude,
                longitude=doctor.longitude,
                address=doctor.address,
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

    if booking.status not in ["confirmed", "arrived"]:
        raise HTTPException(status_code=400, detail="Booking already cancelled or completed")

    booking.status = "cancelled"

    slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    if slot:
        slot.status = "available"

    db.commit()

    notify_booking_cancelled(str(booking_id), cancelled_by="patient", db=db)

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

        waiting_user = db.query(User).filter(User.id == waiting.patient_id).first()
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


@router.get("/{booking_id}/queue-position")
def get_queue_position(
    booking_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import date as date_type

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if str(booking.patient_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your booking")

    slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    if not slot or slot.date != date_type.today():
        return {"position": None, "total": None, "estimated_wait_minutes": None, "is_today": False}

    all_today = (
        db.query(Booking)
        .join(Slot, Slot.id == Booking.slot_id)
        .filter(
            Booking.doctor_id == booking.doctor_id,
            Slot.date == date_type.today(),
            Booking.status.in_(["confirmed", "arrived", "in_consultation"]),
        )
        .order_by(Slot.start_time)
        .all()
    )

    position = next(
        (i + 1 for i, b in enumerate(all_today) if str(b.id) == str(booking_id)),
        None
    )

    doctor = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
    slot_duration = doctor.slot_duration_minutes if doctor else 20
    estimated_wait = (position - 1) * slot_duration if position else None

    return {
        "position": position,
        "total": len(all_today),
        "estimated_wait_minutes": estimated_wait,
        "is_today": True,
    }


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