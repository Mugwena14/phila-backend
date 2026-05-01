import anthropic
import json
from app.core.config import settings
from app.db.database import SessionLocal
from app.models.user import User
from app.models.booking import Booking
from app.models.slot import Slot
from app.models.doctor import Doctor
from app.models.patient_health_summary import PatientHealthSummary
from sqlalchemy import text
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def build_patient_summary(patient_id: str, db) -> dict:
    """
    Builds a health summary for a single patient
    from their full booking + intake history.
    """
    # Get all completed bookings
    bookings = (
        db.query(Booking)
        .filter(
            Booking.patient_id == patient_id,
            Booking.status.in_(["confirmed", "completed"])
        )
        .all()
    )

    if not bookings:
        return {}

    # Build visit history
    visits = []
    specialties_seen = set()
    all_medications = []

    for b in bookings:
        slot = db.query(Slot).filter(Slot.id == b.slot_id).first()
        doctor = db.query(Doctor).filter(Doctor.id == b.doctor_id).first()

        if doctor:
            specialties_seen.add(doctor.specialty)

        # Get intake brief if exists
        brief = db.execute(
            text("SELECT * FROM intake_briefs WHERE booking_id = :id"),
            {"id": str(b.id)}
        ).fetchone()

        visit = {
            "date": str(slot.date) if slot else "unknown",
            "specialty": doctor.specialty if doctor else "unknown",
            "reason": b.reason or "not provided",
        }

        if brief:
            brief_dict = dict(brief._mapping)
            visit["main_concern"] = brief_dict.get("main_concern", "")
            # Extract medications
            try:
                meds = json.loads(brief_dict.get("medications", "[]"))
                if meds:
                    all_medications.extend(meds)
                    visit["medications"] = meds
            except Exception:
                pass

        visits.append(visit)

    # Sort by date descending
    visits.sort(key=lambda x: x.get("date", ""), reverse=True)

    return {
        "total_visits": len(visits),
        "last_visit_date": visits[0]["date"] if visits else None,
        "specialties_seen": list(specialties_seen),
        "medications_detected": list(set(all_medications)),
        "visit_history": visits,
    }


def save_patient_summary(patient_id: str, summary: dict, db) -> None:
    """Save or update patient health summary."""
    existing = db.query(PatientHealthSummary).filter(
        PatientHealthSummary.patient_id == patient_id
    ).first()

    if existing:
        existing.total_visits = str(summary.get("total_visits", 0))
        existing.last_visit_date = summary.get("last_visit_date")
        existing.specialties_seen = json.dumps(summary.get("specialties_seen", []))
        existing.medications_detected = json.dumps(summary.get("medications_detected", []))
        existing.last_scanned_at = datetime.now()
    else:
        db.add(PatientHealthSummary(
            patient_id=patient_id,
            total_visits=str(summary.get("total_visits", 0)),
            last_visit_date=summary.get("last_visit_date"),
            specialties_seen=json.dumps(summary.get("specialties_seen", [])),
            medications_detected=json.dumps(summary.get("medications_detected", [])),
            last_scanned_at=datetime.now(),
        ))
    db.commit()