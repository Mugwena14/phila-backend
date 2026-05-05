from app.celery_app import celery_app
from app.db.database import SessionLocal
from app.models.notification import Notification
from app.models.booking import Booking
from app.models.slot import Slot
from app.models.doctor import Doctor
from app.models.user import User
from datetime import datetime, timezone, timedelta, date
from sqlalchemy import text
import logging

logger = logging.getLogger(__name__)


# ─── Base helper ──────────────────────────────────────────────────────────────

def create_notification(
    db,
    user_id: str,
    type: str,
    title: str,
    body: str,
    action_type: str = None,
    action_data: dict = None,
):
    notif = Notification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        action_type=action_type,
        action_data=action_data or {},
    )
    db.add(notif)
    db.commit()
    return notif


# ─── Sync helpers (called directly from routes) ───────────────────────────────

def notify_booking_confirmed(booking_id: str, db):
    """Called from bookings route when booking is created."""
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return

    slot   = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    doctor = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
    patient = db.query(User).filter(User.id == booking.patient_id).first()
    if not all([slot, doctor, patient]):
        return

    appt_date = slot.date.strftime("%A, %d %B")
    appt_time = slot.start_time.strftime("%H:%M")

    # Patient
    create_notification(
        db=db,
        user_id=str(booking.patient_id),
        type="booking_confirmed",
        title="Booking confirmed ✓",
        body=f"Your appointment with {doctor.practice_name} is on {appt_date} at {appt_time}.",
        action_type="Appointments",
        action_data={"booking_id": booking_id},
    )

    # Doctor
    create_notification(
        db=db,
        user_id=str(doctor.user_id),
        type="new_booking",
        title="New booking",
        body=f"{patient.full_name} booked a slot on {appt_date} at {appt_time}.",
        action_type="bookings",
        action_data={"booking_id": booking_id},
    )


def notify_booking_cancelled(booking_id: str, cancelled_by: str, db):
    """Called from bookings route when status changes to cancelled."""
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return

    slot    = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    doctor  = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
    patient = db.query(User).filter(User.id == booking.patient_id).first()
    if not all([slot, doctor, patient]):
        return

    appt_date = slot.date.strftime("%d %b")
    appt_time = slot.start_time.strftime("%H:%M")

    if cancelled_by == "patient":
        create_notification(
            db=db,
            user_id=str(doctor.user_id),
            type="booking_cancelled",
            title="Booking cancelled",
            body=f"{patient.full_name} cancelled their {appt_date} at {appt_time} slot.",
            action_type="bookings",
            action_data={"date": str(slot.date)},
        )
    else:
        create_notification(
            db=db,
            user_id=str(booking.patient_id),
            type="booking_cancelled",
            title="Appointment cancelled",
            body=f"Your {appt_date} at {appt_time} appointment with {doctor.practice_name} was cancelled.",
            action_type="Appointments",
            action_data={"booking_id": booking_id},
        )


def notify_patient_checkin(booking_id: str, db):
    """Called when patient checks in via WhatsApp."""
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return

    slot    = db.query(Slot).filter(Slot.id == booking.slot_id).first()
    doctor  = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
    patient = db.query(User).filter(User.id == booking.patient_id).first()
    if not all([slot, doctor, patient]):
        return

    appt_time = slot.start_time.strftime("%H:%M")

    create_notification(
        db=db,
        user_id=str(doctor.user_id),
        type="patient_checkin",
        title="Patient checked in",
        body=f"{patient.full_name} checked in for their {appt_time} appointment.",
        action_type="waiting_room",
        action_data={"booking_id": booking_id},
    )


def notify_document_ready(booking_id: str, document_type: str, patient_name: str, db):
    """Called when a document is generated."""
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        return

    doctor = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
    if not doctor:
        return

    create_notification(
        db=db,
        user_id=str(doctor.user_id),
        type="document_ready",
        title="Document ready",
        body=f"{document_type} for {patient_name} has been generated.",
        action_type="documents",
        action_data={"booking_id": booking_id},
    )


def notify_triage_summary(patient_id: str, summary: str, db):
    """Called when WhatsApp intake/triage is complete."""
    short = summary[:120] + "..." if len(summary) > 120 else summary
    create_notification(
        db=db,
        user_id=patient_id,
        type="triage_summary",
        title="Your triage summary is ready",
        body=short,
        action_type="Appointments",
        action_data={},
    )


def notify_new_doctor_nearby(patient_id: str, doctor_name: str, specialty: str, city: str, db):
    """Called when a new doctor registers — fired for nearby patients."""
    create_notification(
        db=db,
        user_id=patient_id,
        type="new_doctor_nearby",
        title=f"New {specialty} near you",
        body=f"{doctor_name} just joined Phila in {city}. Book now.",
        action_type="Search",
        action_data={"specialty": specialty},
    )


# ─── Celery tasks (scheduled) ─────────────────────────────────────────────────

@celery_app.task
def notify_appointment_reminder_task(booking_id: str, hours_before: int):
    """Scheduled at booking creation for 24hr and 1hr before."""
    db = SessionLocal()
    try:
        booking = db.query(Booking).filter(Booking.id == booking_id).first()
        if not booking or booking.status != "confirmed":
            return

        slot   = db.query(Slot).filter(Slot.id == booking.slot_id).first()
        doctor = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
        if not all([slot, doctor]):
            return

        appt_time = slot.start_time.strftime("%H:%M")

        if hours_before == 1:
            title = "Appointment in 1 hour ⏰"
            body  = f"Your appointment with {doctor.practice_name} starts at {appt_time}. Head out now!"
        else:
            title = "Appointment tomorrow 📅"
            body  = f"Reminder: {doctor.practice_name} tomorrow at {appt_time}."

        create_notification(
            db=db,
            user_id=str(booking.patient_id),
            type="appointment_reminder",
            title=title,
            body=body,
            action_type="Appointments",
            action_data={"booking_id": booking_id},
        )
        logger.info(f"Reminder notification ({hours_before}hr) for booking {booking_id}")
    except Exception as e:
        logger.error(f"Error in reminder notification: {e}")
    finally:
        db.close()


@celery_app.task
def check_no_shows():
    """
    Runs every 15 min via Celery beat.
    Marks confirmed bookings as no_show if slot passed 30 min ago.
    """
    db = SessionLocal()
    try:
        past_bookings = db.execute(text("""
            SELECT b.id, b.doctor_id, b.patient_id, b.slot_id
            FROM bookings b
            JOIN slots s ON s.id = b.slot_id
            WHERE b.status = 'confirmed'
            AND s.date = CURRENT_DATE
            AND s.start_time <= (NOW() - INTERVAL '30 minutes')::time
        """)).fetchall()

        for row in past_bookings:
            booking = db.query(Booking).filter(Booking.id == row.id).first()
            if not booking:
                continue

            booking.status = "no_show"
            db.commit()

            patient = db.query(User).filter(User.id == booking.patient_id).first()
            doctor  = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
            slot    = db.query(Slot).filter(Slot.id == booking.slot_id).first()

            if patient and doctor and slot:
                create_notification(
                    db=db,
                    user_id=str(doctor.user_id),
                    type="no_show",
                    title="No-show",
                    body=f"{patient.full_name} did not arrive for their {slot.start_time.strftime('%H:%M')} appointment.",
                    action_type="bookings",
                    action_data={"booking_id": str(booking.id)},
                )
                logger.info(f"No-show for booking {booking.id}")
    except Exception as e:
        logger.error(f"Error in check_no_shows: {e}")
    finally:
        db.close()


@celery_app.task
def send_daily_summary():
    """Runs every day at 20:00 via Celery beat."""
    db = SessionLocal()
    try:
        today   = date.today()
        doctors = db.query(Doctor).filter(Doctor.is_active == True).all()

        for doctor in doctors:
            bookings = db.query(Booking).join(Slot).filter(
                Booking.doctor_id == doctor.id,
                Slot.date == today,
            ).all()

            total     = len(bookings)
            completed = len([b for b in bookings if b.status == "completed"])
            no_shows  = len([b for b in bookings if b.status == "no_show"])
            revenue   = completed * doctor.consultation_fee

            if total == 0:
                continue

            create_notification(
                db=db,
                user_id=str(doctor.user_id),
                type="daily_summary",
                title="Today's summary",
                body=f"{completed} consultation{'s' if completed != 1 else ''} · {no_shows} no-show{'s' if no_shows != 1 else ''} · R{revenue:.0f} earned.",
                action_type="dashboard",
                action_data={"date": str(today)},
            )

        logger.info(f"Daily summaries sent to {len(doctors)} doctors")
    except Exception as e:
        logger.error(f"Error in send_daily_summary: {e}")
    finally:
        db.close()


@celery_app.task
def check_slots_low():
    """Runs every day at 08:00 via Celery beat."""
    db = SessionLocal()
    try:
        today    = date.today()
        week_end = today + timedelta(days=7)
        doctors  = db.query(Doctor).filter(Doctor.is_active == True).all()

        for doctor in doctors:
            count = db.query(Slot).filter(
                Slot.doctor_id == doctor.id,
                Slot.status == "available",
                Slot.date >= today,
                Slot.date <= week_end,
            ).count()

            if count < 3:
                create_notification(
                    db=db,
                    user_id=str(doctor.user_id),
                    type="slots_low",
                    title="Slots running low",
                    body=f"You only have {count} available slot{'s' if count != 1 else ''} this week. Generate more to keep bookings open.",
                    action_type="schedule",
                    action_data={},
                )

        logger.info("Slots low check complete")
    except Exception as e:
        logger.error(f"Error in check_slots_low: {e}")
    finally:
        db.close()