import anthropic
import json
from app.core.config import settings
from app.services.conversation_state import (
    get_conversation,
    save_conversation,
    clear_conversation,
)
from app.services.crisis_detector import detect_crisis
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

MAX_TURNS = 6

INTAKE_SYSTEM_PROMPT = """You are Phila, a friendly medical intake assistant for a South African private healthcare platform.

Your job is to collect key information from a patient before their doctor's appointment — in a warm, conversational way.

You need to gather:
1. Main concern / reason for visit
2. How long they've had this problem (duration)
3. Severity on a scale of 1-10
4. Any current medications or chronic conditions
5. Any allergies (medications or otherwise)
6. Anything else they want the doctor to know

Rules:
- Ask ONE question at a time — never overwhelm the patient
- Be warm, empathetic and professional
- Keep responses short — this is WhatsApp, not a form
- Accept responses in English, Zulu, Xhosa, Sotho or Afrikaans — reply in the same language
- After 6 turns maximum, wrap up warmly and tell them the doctor has been briefed
- NEVER give medical advice or diagnoses
- If a patient seems distressed, be extra gentle

You are NOT a doctor. You are a friendly intake assistant."""


def start_intake(
    phone: str,
    booking_id: str,
    patient_name: str,
    doctor_name: str,
    appt_date: str,
    appt_time: str,
) -> None:
    """
    Initialise conversation state when intake begins.
    Called by Celery task — the first WhatsApp message
    is sent by the task itself.
    """
    state = get_conversation(phone)
    state["stage"] = "in_progress"
    state["booking_id"] = booking_id
    state["patient_name"] = patient_name
    state["doctor_name"] = doctor_name
    state["appt_date"] = appt_date
    state["appt_time"] = appt_time
    state["turn"] = 0
    state["messages"] = []
    save_conversation(phone, state)


def process_intake_reply(phone: str, patient_message: str) -> str:
    """
    Process a patient's WhatsApp reply during intake.
    Returns the next message to send back to the patient.
    """
    state = get_conversation(phone)

    if state["stage"] not in ["in_progress"]:
        return (
            "Hi! To start your intake, please book an appointment "
            "through the Phila app first. 🏥"
        )

    patient_name = state.get("patient_name", "there")

    # Run crisis detection on every message
    crisis = detect_crisis(patient_message, patient_name)

    if crisis["crisis_detected"]:
        state["crisis_flagged"] = True
        state["crisis_severity"] = crisis["severity"]
        save_conversation(phone, state)
        _flag_crisis_in_db(state.get("booking_id"), crisis["severity"])

        if crisis["severity"] == "high":
            clear_conversation(phone)
            return crisis["response_text"]

        elif crisis["severity"] == "low":
            _send_low_severity_note(phone, crisis["response_text"])

    # Add patient message to conversation history
    state["messages"].append({
        "role": "user",
        "content": patient_message
    })
    state["turn"] += 1

    # Check if we've reached max turns
    if state["turn"] >= MAX_TURNS:
        return _finalise_intake(phone, state)

    # Get Claude's next response
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system=INTAKE_SYSTEM_PROMPT,
            messages=state["messages"],
        )

        assistant_message = response.content[0].text

        state["messages"].append({
            "role": "assistant",
            "content": assistant_message
        })

        save_conversation(phone, state)
        return assistant_message

    except Exception as e:
        logger.error(f"Claude API error in intake: {e}")
        return (
            "Sorry, I'm having a technical issue. "
            "Your doctor will still see you — no worries! 🙏"
        )


def _finalise_intake(phone: str, state: dict) -> str:
    """
    Called when max turns reached.
    Makes a second Claude call to produce a structured brief.
    Saves brief to database.
    Triggers medication extraction task.
    """
    patient_first = state.get("patient_name", "").split()[0]
    doctor = state.get("doctor_name", "your doctor")
    appt_date = state.get("appt_date", "")
    appt_time = state.get("appt_time", "")
    booking_id = state.get("booking_id")

    # Build success message upfront — always returned regardless of brief outcome
    success_message = (
        f"Thank you {patient_first}! 🙏\n\n"
        f"I've sent your information to *{doctor}* so they'll be "
        f"fully prepared for your appointment.\n\n"
        f"See you on {appt_date} at {appt_time}. Take care! 💙"
    )

    try:
        brief_response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system="""You are a medical scribe. Extract a structured patient intake brief from this conversation.

Return ONLY valid JSON with this exact structure — no markdown, no backticks, no explanation:
{
  "main_concern": "string",
  "duration": "string",
  "severity": "number 1-10 or unknown",
  "medications": ["list", "or", "empty array"],
  "allergies": ["list", "or", "empty array"],
  "additional_notes": "string or empty string",
  "language_used": "English/Zulu/Xhosa/Afrikaans/Other",
  "crisis_flagged": false
}

Be concise and clinical. This goes directly to the doctor.""",
            messages=[
                {
                    "role": "user",
                    "content": f"Extract brief from this intake conversation:\n\n{json.dumps(state['messages'], indent=2)}"
                }
            ]
        )

        brief_text = brief_response.content[0].text.strip()

        # Strip markdown backticks if Claude wrapped the JSON
        if brief_text.startswith("```"):
            brief_text = brief_text.split("```")[1]
            if brief_text.startswith("json"):
                brief_text = brief_text[4:]
        brief_text = brief_text.strip()

        brief_data = json.loads(brief_text)
        brief_data["crisis_flagged"] = state.get("crisis_flagged", False)

        _save_brief_to_db(booking_id, brief_data)
        logger.info(f"Brief saved successfully for {phone}")

        # Trigger medication extraction — runs async via Celery
        try:
            from app.tasks.health_tasks import extract_and_save_medications
            extract_and_save_medications.delay(booking_id)
            logger.info(f"Medication extraction queued for booking {booking_id}")
        except Exception as e:
            logger.error(f"Error queuing medication extraction: {e}")

    except Exception as e:
        logger.error(f"Error generating brief for {phone}: {e}")
        # Brief failed but patient still gets the proper success message

    finally:
        clear_conversation(phone)

    return success_message


def _save_brief_to_db(booking_id: str, brief_data: dict) -> None:
    """Save structured brief to intake_briefs table."""
    from app.db.database import SessionLocal
    from sqlalchemy import text
    import uuid

    db = SessionLocal()
    try:
        db.execute(
            text("""
                INSERT INTO intake_briefs
                    (id, booking_id, main_concern, duration, severity,
                     medications, allergies, additional_notes,
                     language_used, crisis_flagged, raw_brief)
                VALUES
                    (:id, :booking_id, :main_concern, :duration, :severity,
                     :medications, :allergies, :additional_notes,
                     :language_used, :crisis_flagged, :raw_brief)
                ON CONFLICT (booking_id) DO UPDATE SET
                    main_concern = EXCLUDED.main_concern,
                    duration = EXCLUDED.duration,
                    severity = EXCLUDED.severity,
                    medications = EXCLUDED.medications,
                    allergies = EXCLUDED.allergies,
                    additional_notes = EXCLUDED.additional_notes,
                    language_used = EXCLUDED.language_used,
                    crisis_flagged = EXCLUDED.crisis_flagged,
                    raw_brief = EXCLUDED.raw_brief
            """),
            {
                "id": str(uuid.uuid4()),
                "booking_id": booking_id,
                "main_concern": brief_data.get("main_concern", ""),
                "duration": brief_data.get("duration", ""),
                "severity": str(brief_data.get("severity", "")),
                "medications": json.dumps(brief_data.get("medications", [])),
                "allergies": json.dumps(brief_data.get("allergies", [])),
                "additional_notes": brief_data.get("additional_notes", ""),
                "language_used": brief_data.get("language_used", "English"),
                "crisis_flagged": brief_data.get("crisis_flagged", False),
                "raw_brief": json.dumps(brief_data),
            }
        )
        db.commit()
        logger.info(f"Brief saved for booking {booking_id}")
    except Exception as e:
        logger.error(f"Error saving brief: {e}")
    finally:
        db.close()


def _flag_crisis_in_db(booking_id: str, severity: str) -> None:
    """Flag crisis on the booking record."""
    if not booking_id:
        return
    from app.db.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        db.execute(
            text("UPDATE bookings SET crisis_flag = :severity WHERE id = :id"),
            {"severity": severity, "id": booking_id}
        )
        db.commit()
    except Exception as e:
        logger.error(f"Error flagging crisis: {e}")
    finally:
        db.close()


def _send_low_severity_note(phone: str, note: str) -> None:
    """Send a gentle support note without stopping the intake."""
    from app.services.whatsapp import send_whatsapp_message
    send_whatsapp_message(phone, note)