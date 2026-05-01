from app.celery_app import celery_app
from app.services.whatsapp import send_whatsapp_message
from app.db.database import SessionLocal
from app.models.booking import Booking
from app.models.slot import Slot
from app.models.doctor import Doctor
from app.models.user import User
import logging

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def send_intake_whatsapp(self, booking_id: str):
    """
    Fires 10 seconds after booking confirmed.
    Sends first intake question + initialises conversation state.
    """
    db = SessionLocal()
    try:
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:
            logger.error(f"Booking {booking_id} not found")
            return

        patient = db.query(User).filter(
            User.id == booking.patient_id
        ).first()
        slot = db.query(Slot).filter(
            Slot.id == booking.slot_id
        ).first()
        doctor = db.query(Doctor).filter(
            Doctor.id == booking.doctor_id
        ).first()

        if not all([patient, slot, doctor]):
            logger.error(f"Missing data for booking {booking_id}")
            return

        appt_date = slot.date.strftime("%A, %d %B")
        appt_time = slot.start_time.strftime("%H:%M")

        # Initialise conversation state in Redis
        from app.services.intake_agent import start_intake
        start_intake(
            phone=patient.phone,
            booking_id=booking_id,
            patient_name=patient.full_name,
            doctor_name=doctor.practice_name,
            appt_date=appt_date,
            appt_time=appt_time,
        )

        # Send the first intake message
        message = (
            f"Hi {patient.full_name.split()[0]}! 👋\n\n"
            f"You're booked with *{doctor.practice_name}* on "
            f"*{appt_date} at {appt_time}*.\n\n"
            f"To help your doctor prepare, I have a few quick questions "
            f"about your visit.\n\n"
            f"*What is your main concern for this appointment?*\n\n"
            f"_(Reply in English, Zulu, Xhosa or Afrikaans)_"
        )

        success = send_whatsapp_message(patient.phone, message)

        if not success:
            raise self.retry(countdown=120)

        logger.info(f"Intake started for booking {booking_id}")

    except Exception as e:
        logger.error(f"Error in send_intake_whatsapp: {e}")
        raise self.retry(exc=e, countdown=120)
    finally:
        db.close()



@celery_app.task(bind=True, max_retries=3)
def send_appointment_reminder(self, booking_id: str, hours_before: int):
    """
    Sends a reminder message X hours before the appointment.
    Hours before: 72, 48, 24, or 2.
    """
    db = SessionLocal()
    try:
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking or booking.status != "confirmed":
            logger.info(
                f"Skipping reminder for booking {booking_id} "
                f"— status: {booking.status if booking else 'not found'}"
            )
            return

        patient = db.query(User).filter(
            User.id == booking.patient_id
        ).first()
        slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
        doctor = db.query(Doctor).filter(
            Doctor.id == booking.doctor_id
        ).first()

        if not all([patient, slot, doctor]):
            return

        appt_date = slot.date.strftime("%A, %d %B")
        appt_time = slot.start_time.strftime("%H:%M")

        # Different message based on urgency
        if hours_before == 2:
            message = (
                f"⏰ Reminder: You have an appointment with "
                f"{doctor.practice_name} *today at {appt_time}*.\n\n"
                f"Please reply *YES* to confirm you're coming "
                f"or *NO* to cancel so we can open your slot."
            )
        elif hours_before == 24:
            message = (
                f"📅 Your appointment with {doctor.practice_name} is "
                f"*tomorrow at {appt_time}*.\n\n"
                f"Reply *YES* to confirm or *NO* to cancel."
            )
        else:
            message = (
                f"Hi {patient.full_name.split()[0]}! Just a reminder that "
                f"you have an appointment with {doctor.practice_name} on "
                f"{appt_date} at {appt_time}.\n\n"
                f"Reply *YES* to confirm or *NO* to cancel."
            )

        success = send_whatsapp_message(patient.phone, message)

        if not success:
            raise self.retry(countdown=300)

    except Exception as e:
        logger.error(f"Error sending reminder for booking {booking_id}: {e}")
        raise self.retry(exc=e, countdown=300)
    finally:
        db.close()



@celery_app.task(bind=True, max_retries=3)
def send_followup_whatsapp(self, booking_id: str):
    """
    Fires 3 days after appointment.
    Checks in with patient on how they're feeling.
    """
    db = SessionLocal()
    try:
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:
            return

        patient = db.query(User).filter(
            User.id == booking.patient_id
        ).first()
        doctor = db.query(Doctor).filter(
            Doctor.id == booking.doctor_id
        ).first()

        if not all([patient, doctor]):
            return

        # Store follow-up state in Redis so we can process reply
        import redis
        import json
        from app.core.config import settings as app_settings
        r = redis.from_url(app_settings.REDIS_URL, decode_responses=True)
        r.setex(
            f"followup:{patient.phone}",
            86400,  # 24hr window to reply
            json.dumps({
                "booking_id": booking_id,
                "patient_name": patient.full_name,
                "doctor_name": doctor.practice_name,
            })
        )

        first_name = patient.full_name.split()[0]
        message = (
            f"Hi {first_name}! 👋\n\n"
            f"It's been a few days since your visit with "
            f"*{doctor.practice_name}*.\n\n"
            f"How are you feeling? Is your main concern improving? 💙"
        )

        success = send_whatsapp_message(patient.phone, message)
        if not success:
            raise self.retry(countdown=300)

        logger.info(f"Follow-up sent for booking {booking_id}")

    except Exception as e:
        logger.error(f"Error in follow-up task: {e}")
        raise self.retry(exc=e, countdown=300)
    finally:
        db.close()

@celery_app.task(bind=True, max_retries=1)
def check_intake_completion(self, booking_id: str):
    """
    Fires 30 minutes after intake message sent.
    If patient hasn't completed intake, bump risk score +20
    and send a gentle nudge.
    """
    import redis
    import json
    from app.core.config import settings as app_settings
    from sqlalchemy import text

    db = SessionLocal()
    try:
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking or booking.status != "confirmed":
            return

        patient = db.query(User).filter(
            User.id == booking.patient_id
        ).first()

        if not patient:
            return

        # Check if intake brief exists in DB
        result = db.execute(
            text("SELECT id FROM intake_briefs WHERE booking_id = :id"),
            {"id": booking_id}
        ).fetchone()

        if result:
            # Intake completed — nothing to do
            logger.info(f"Intake already complete for booking {booking_id}")
            return

        # Check Redis conversation state
        r = redis.from_url(app_settings.REDIS_URL, decode_responses=True)
        state_data = r.get(f"intake:{patient.phone}")

        if state_data:
            state = json.loads(state_data)
            if state.get("stage") == "in_progress" and state.get("turn", 0) > 0:
                # Patient started but didn't finish — don't nudge yet
                logger.info(f"Intake in progress for booking {booking_id}")
                return

        # Intake not started or abandoned — bump risk score
        current_risk = int(booking.risk_score or "0")
        new_risk = min(current_risk + 20, 100)
        booking.risk_score = str(new_risk)
        db.commit()

        logger.info(
            f"Risk score bumped from {current_risk} to {new_risk} "
            f"for booking {booking_id} — intake incomplete"
        )

        # Send a gentle nudge
        send_whatsapp_message(
            patient.phone,
            f"Hi {patient.full_name.split()[0]}! 👋\n\n"
            f"We noticed you haven't completed your pre-appointment "
            f"questions yet.\n\n"
            f"It only takes 2 minutes and helps your doctor prepare "
            f"for your visit. Reply here to continue! 🏥"
        )

    except Exception as e:
        logger.error(f"Error in check_intake_completion: {e}")
    finally:
        db.close()