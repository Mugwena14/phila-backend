import anthropic
import json
from app.core.config import settings
from app.services.crisis_detector import detect_crisis
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def assess_followup_response(
    patient_message: str,
    patient_name: str,
    doctor_name: str,
    booking_id: str,
) -> dict:
    """
    Assesses a patient's follow-up response.
    Returns action to take: improving / not_improving / no_reply
    """
    # Always run crisis detection on follow-up responses
    crisis = detect_crisis(patient_message, patient_name)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system="""You assess patient follow-up responses after a doctor's appointment.

Return ONLY valid JSON:
{
  "outcome": "improving" or "not_improving" or "unclear",
  "should_rebook": true or false,
  "response_message": "warm message to send to patient (max 2 sentences)"
}

Rules:
- "improving": patient indicates they feel better or the issue is resolving
- "not_improving": patient indicates no change or getting worse
- "unclear": cannot determine from the message
- suggest rebooking if not_improving or unclear""",
            messages=[
                {"role": "user", "content": patient_message}
            ]
        )

        result = json.loads(response.content[0].text)
        result["crisis"] = crisis

        # Save outcome to database
        _save_outcome(booking_id, result["outcome"])

        return result

    except Exception as e:
        logger.error(f"Follow-up agent error: {e}")
        return {
            "outcome": "unclear",
            "should_rebook": False,
            "response_message": "Thank you for letting us know. Take care! 💙",
            "crisis": crisis,
        }


def _save_outcome(booking_id: str, outcome: str) -> None:
    """Tag outcome on the booking record."""
    from app.db.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE bookings SET outcome = :outcome WHERE id = :id"),
            {"outcome": outcome, "id": booking_id}
        )
        db.commit()
    except Exception as e:
        logger.error(f"Error saving outcome: {e}")
    finally:
        db.close()