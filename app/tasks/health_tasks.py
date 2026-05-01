from app.celery_app import celery_app
from app.services.whatsapp import send_whatsapp_message
from app.db.database import SessionLocal
from app.models.user import User
from app.models.booking import Booking
from app.models.patient_profile import PatientProfile
from app.models.patient_medication import PatientMedication
from sqlalchemy import text
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@celery_app.task
def run_weekly_health_scan():
    """
    Runs every Sunday at 08:00.
    For every active patient:
    1. Builds health summary
    2. Detects care gaps
    3. Sends personalised WhatsApp nudge for top gap
    """
    from app.services.health_memory import build_patient_summary, save_patient_summary
    from app.services.care_gap_detector import detect_care_gaps

    db = SessionLocal()
    try:
        # Get all patients who have at least one booking
        patients = db.execute(
            text("""
                SELECT DISTINCT u.id, u.full_name, u.phone
                FROM users u
                INNER JOIN bookings b ON b.patient_id = u.id
                WHERE u.role = 'patient'
            """)
        ).fetchall()

        logger.info(f"Weekly health scan — processing {len(patients)} patients")

        for p in patients:
            patient_id = str(p.id)
            patient_name = p.full_name
            phone = p.phone

            try:
                # Build health summary
                summary = build_patient_summary(patient_id, db)
                if not summary:
                    continue

                save_patient_summary(patient_id, summary, db)

                # Get patient profile for age/gender
                profile = db.query(PatientProfile).filter(
                    PatientProfile.user_id == patient_id
                ).first()

                age = 30  # default
                gender = "unknown"

                if profile and profile.date_of_birth:
                    try:
                        birth = datetime.strptime(profile.date_of_birth, "%Y-%m-%d")
                        age = datetime.now().year - birth.year
                    except Exception:
                        pass

                # Detect care gaps
                gaps = detect_care_gaps(
                    patient_id=patient_id,
                    patient_name=patient_name,
                    age=age,
                    gender=gender,
                    visit_history=summary.get("visit_history", []),
                    last_visit_date=summary.get("last_visit_date"),
                )

                # Send WhatsApp for the top gap only
                if gaps:
                    top_gap = gaps[0]
                    first_name = patient_name.split()[0]
                    message = (
                        f"Hi {first_name}! 💙\n\n"
                        f"*{top_gap['type']}*\n"
                        f"{top_gap['message']}\n\n"
                        f"Open Phila to book with the right doctor. 🏥"
                    )
                    send_whatsapp_message(phone, message)
                    logger.info(f"Care gap nudge sent to {phone}: {top_gap['type']}")

            except Exception as e:
                logger.error(f"Error processing patient {patient_id}: {e}")
                continue

    finally:
        db.close()


@celery_app.task
def run_prescription_refill_check():
    """
    Runs every day at 09:00.
    Checks all patients for upcoming medication refills.
    Sends WhatsApp 14 days before estimated refill date.
    """
    from app.services.prescription_refill import estimate_refill_date

    db = SessionLocal()
    try:
        today = datetime.now().date()
        notify_date = today + timedelta(days=14)

        # Find medications due for refill in 14 days
        medications = db.query(PatientMedication).filter(
            PatientMedication.estimated_refill_date == str(notify_date),
            PatientMedication.refill_notified == False,
        ).all()

        logger.info(f"Prescription refill check — {len(medications)} due")

        for med in medications:
            patient = db.query(User).filter(
                User.id == med.patient_id
            ).first()

            if not patient:
                continue

            first_name = patient.full_name.split()[0]

            message = (
                f"Hi {first_name}! 💊\n\n"
                f"Your *{med.medication_name}* may be running low soon.\n\n"
                f"Would you like to book a refill appointment? "
                f"Open Phila to find your doctor. 🏥"
            )

            success = send_whatsapp_message(patient.phone, message)

            if success:
                med.refill_notified = True
                db.commit()
                logger.info(f"Refill reminder sent to {patient.phone} for {med.medication_name}")

    finally:
        db.close()


@celery_app.task
def extract_and_save_medications(booking_id: str):
    """
    Called after intake is complete.
    Extracts chronic medications from brief and saves to DB.
    """
    from app.services.prescription_refill import (
        extract_medications_from_brief,
        estimate_refill_date,
    )

    db = SessionLocal()
    try:
        # Get the intake brief
        brief = db.execute(
            text("SELECT * FROM intake_briefs WHERE booking_id = :id"),
            {"id": booking_id}
        ).fetchone()

        if not brief:
            return

        brief_dict = dict(brief._mapping)
        booking = db.query(Booking).filter(
            Booking.id == booking_id
        ).first()

        if not booking:
            return

        # Get last visit date from slot
        from app.models.slot import Slot
        slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
        last_prescribed = str(slot.date) if slot else str(datetime.now().date())

        # Extract medications from raw brief
        raw_brief = brief_dict.get("raw_brief", "{}")
        medications = extract_medications_from_brief(raw_brief)

        for med_name in medications:
            # Check if already tracked
            existing = db.query(PatientMedication).filter(
                PatientMedication.patient_id == booking.patient_id,
                PatientMedication.medication_name == med_name,
            ).first()

            refill_date = estimate_refill_date(med_name, last_prescribed)

            if existing:
                existing.last_prescribed_date = last_prescribed
                existing.estimated_refill_date = refill_date
                existing.refill_notified = False
            else:
                db.add(PatientMedication(
                    patient_id=booking.patient_id,
                    booking_id=booking_id,
                    medication_name=med_name,
                    last_prescribed_date=last_prescribed,
                    estimated_refill_date=refill_date,
                    refill_notified=False,
                ))

        db.commit()
        logger.info(f"Medications extracted for booking {booking_id}: {medications}")

    except Exception as e:
        logger.error(f"Error extracting medications: {e}")
    finally:
        db.close()