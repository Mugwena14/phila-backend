from sqlalchemy.orm import Session
from uuid import UUID
from app.models.booking import Booking

def calculate_risk_score(
    db: Session,
    patient_id: UUID,
    days_until_appointment: int,
) -> int:
    score = 0

    # Past no-show history — biggest signal
    past_noshows = (
        db.query(Booking)
        .filter(
            Booking.patient_id == patient_id,
            Booking.status == "no_show",
        )
        .count()
    )
    score += min(past_noshows * 25, 50)

    # First time patient — moderate risk
    past_bookings = (
        db.query(Booking)
        .filter(Booking.patient_id == patient_id)
        .count()
    )
    if past_bookings == 0:
        score += 20

    # Far away appointment — higher chance of forgetting
    if days_until_appointment > 7:
        score += 15
    elif days_until_appointment > 3:
        score += 10

    return min(score, 100)


def get_risk_label(score: int) -> str:
    if score < 30:
        return "low"
    elif score < 65:
        return "medium"
    return "high"