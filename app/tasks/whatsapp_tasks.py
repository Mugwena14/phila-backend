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
    Fires 60 seconds after booking confirmed.
    Sends the first intake message to the patient via WhatsApp.
    """
    db = SessionLocal()
    try:
        # Fetch booking with related data
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:
            logger.error(f"Booking {booking_id} not found for intake task")
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

        # Format appointment time nicely
        appt_date = slot.date.strftime("%A, %d %B")
        appt_time = slot.start_time.strftime("%H:%M")

        # First intake message
        message = (
            f"Hi {patient.full_name.split()[0]}! 👋\n\n"
            f"You're booked with {doctor.practice_name} on "
            f"{appt_date} at {appt_time}.\n\n"
            f"To help your doctor prepare, can I ask you 5 quick questions "
            f"about your visit.\n\n"
            f"*What is your main concern for this appointment?*\n\n"
            f"_(Reply in English, Zulu, Xhosa or Afrikaans — your choice)_"
        )

        success = send_whatsapp_message(patient.phone, message)

        if success:
            logger.info(
                f"Intake message sent for booking {booking_id} "
                f"to {patient.phone}"
            )
        else:
            # Retry if sending failed
            raise self.retry(countdown=120)

    except Exception as e:
        logger.error(f"Error in send_intake_whatsapp task: {e}")
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