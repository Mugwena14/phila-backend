"""
Walk-in patient welcome comms.

Triggered after a receptionist creates a walk-in booking. Sends a WhatsApp
to the patient's real phone with:
  - Confirmation of the booking (practice + day + time)
  - One-paragraph pitch for the Phila app
  - Download link
  - Sign-up phone number (THE SAME PHONE) so the auth/register claim flow
    can find the WALKIN_+27... user and link the booking on signup

Returns (success, error_message). Either way, the call site writes to
booking_comms_log so the audit trail is complete.

Why not just inline this in the bookings route? Because the same comms
will fire on at least three other events in future phases (booking
created via app for non-Phila patient, booking rescheduled, etc) and
keeping the message text + send logic in one file means we only edit
one place to change the copy.
"""
import logging
from datetime import date, time

from app.services.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)

# Hardcoded for now. Replace when the marketing site / app store URLs exist.
PHILA_DOWNLOAD_URL = "https://philahealth.co.za/app"


def _format_appointment_day(d: date) -> str:
    """Friday, 27 June format - readable on WhatsApp without a year (since
    it's near-term and the year is contextually obvious)."""
    return d.strftime("%A, %d %B")


def _format_appointment_time(t: time) -> str:
    """09:30 format."""
    return t.strftime("%H:%M")


def build_walkin_message(
    patient_name: str,
    practice_name: str,
    appointment_date: date,
    appointment_time: time,
    patient_phone: str,
) -> str:
    """
    Compose the walk-in welcome WhatsApp body. Single source of truth for the
    message copy - change here if you want to tweak wording, not in the route.
    """
    day = _format_appointment_day(appointment_date)
    t = _format_appointment_time(appointment_time)

    return (
        f"Hi {patient_name}, {practice_name} has booked you in for "
        f"{day} at {t}.\n\n"
        f"Phila is the app behind this booking - it's also a full health "
        f"companion that tracks your water, sleep, mood, workouts, and meals, "
        f"plans your day with a built-in task timeline, holds all your doctor's "
        f"notes and prescriptions in one place, and lets you book your next "
        f"visit in seconds.\n\n"
        f"Download Phila: {PHILA_DOWNLOAD_URL}\n"
        f"Sign up with this phone number ({patient_phone}) so we can link your "
        f"booking to your account."
    )


def send_walkin_welcome(
    patient_phone: str,
    patient_name: str,
    practice_name: str,
    appointment_date: date,
    appointment_time: time,
) -> tuple[bool, str | None]:
    """
    Send the walk-in welcome WhatsApp. Returns (success, error_message).
    Wraps the existing whatsapp service so the bookings route doesn't need
    to know how Twilio is wired.
    """
    body = build_walkin_message(
        patient_name=patient_name,
        practice_name=practice_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        patient_phone=patient_phone,
    )

    try:
        success = send_whatsapp_message(patient_phone, body)
        if success:
            return True, None
        return False, "Twilio rejected the send (see backend logs)"
    except Exception as e:
        logger.error(f"Walk-in welcome send to {patient_phone} crashed: {e}")
        return False, f"Unexpected error: {e}"