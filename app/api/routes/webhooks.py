from fastapi import APIRouter, Request, Response
from app.services.whatsapp import parse_incoming_message, send_whatsapp_message
import logging

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Central entry point for all incoming WhatsApp messages.
    Intake conversations take priority over YES/NO routing.
    """
    form_data = await request.form()
    data = parse_incoming_message(dict(form_data))

    from_number = data["from_number"]
    body = data["body"].lower().strip()
    original_body = data["body"].strip()

    logger.info(f"Incoming WhatsApp from {from_number}: {original_body}")

    # ── CHECK INTAKE STATE FIRST ─────────────────────────────────────
    from app.services.conversation_state import get_conversation
    state = get_conversation(from_number)

    # ── DEBUG — log full state so we can see what's happening ────────
    logger.info(f"Conversation state for {from_number}: {state}")
    # ─────────────────────────────────────────────────────────────────

    if state["stage"] == "in_progress":
        await handle_intake_reply(from_number, original_body)
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml",
            status_code=200,
        )

    # Only reach YES/NO handler if NO active intake conversation
    if body in ["yes", "y", "confirm", "yes ✓"]:
        await handle_confirmation(from_number)

    elif body in ["no", "n", "cancel"]:
        await handle_cancellation(from_number)

    else:
        await handle_intake_reply(from_number, original_body)

    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml",
        status_code=200,
    )


async def handle_confirmation(phone: str) -> None:
    """Patient replied YES to appointment reminder."""
    from app.db.database import SessionLocal
    from app.models.user import User
    from app.models.booking import Booking
    from app.models.slot import Slot
    from sqlalchemy import and_
    from datetime import date

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            logger.warning(f"No user found for phone {phone}")
            return

        booking = (
            db.query(Booking)
            .join(Slot)
            .filter(
                and_(
                    Booking.patient_id == user.id,
                    Booking.status == "confirmed",
                    Slot.date >= date.today(),
                )
            )
            .order_by(Slot.date.asc())
            .first()
        )

        if booking:
            send_whatsapp_message(
                phone,
                "✅ Perfect! Your appointment is confirmed. We'll see you soon. "
                "Reply HELP if you need anything."
            )
            logger.info(f"Confirmed booking {booking.id} for {phone}")
        else:
            send_whatsapp_message(
                phone,
                "Thanks for confirming! We couldn't find an upcoming appointment "
                "but you're all good. Open the Phila app to check your bookings."
            )
    finally:
        db.close()


async def handle_cancellation(phone: str) -> None:
    """Patient replied NO — cancel and release slot."""
    from app.db.database import SessionLocal
    from app.models.user import User
    from app.models.booking import Booking
    from app.models.slot import Slot
    from app.models.waitlist import Waitlist
    from sqlalchemy import and_
    from datetime import date

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            return

        booking = (
            db.query(Booking)
            .join(Slot)
            .filter(
                and_(
                    Booking.patient_id == user.id,
                    Booking.status == "confirmed",
                    Slot.date >= date.today(),
                )
            )
            .order_by(Slot.date.asc())
            .first()
        )

        if booking:
            booking.status = "cancelled"
            slot = db.query(Slot).filter(Slot.id == booking.slot_id).first()
            if slot:
                slot.status = "available"
            db.commit()

            send_whatsapp_message(
                phone,
                "Your appointment has been cancelled. "
                "Open Phila to rebook when you're ready. Take care! 🙏"
            )

            if slot:
                waiting = (
                    db.query(Waitlist)
                    .filter(
                        and_(
                            Waitlist.doctor_id == booking.doctor_id,
                            Waitlist.date == slot.date,
                            Waitlist.status == "waiting",
                        )
                    )
                    .order_by(Waitlist.created_at.asc())
                    .first()
                )
                if waiting:
                    waiting_user = db.query(User).filter(
                        User.id == waiting.patient_id
                    ).first()
                    if waiting_user:
                        send_whatsapp_message(
                            waiting_user.phone,
                            f"Good news! A slot just opened up on {slot.date}. "
                            "Open Phila to book it before it's gone! 🎉"
                        )
                        waiting.status = "notified"
                        db.commit()
        else:
            send_whatsapp_message(
                phone,
                "We couldn't find an upcoming appointment to cancel. "
                "Open the Phila app to manage your bookings."
            )
    finally:
        db.close()


async def handle_intake_reply(phone: str, message: str) -> None:
    """
    Routes incoming message to either:
    - Intake agent (if active intake conversation)
    - Follow-up agent (if active follow-up window)
    - Generic response (otherwise)
    """
    import redis
    import json
    from app.core.config import settings as app_settings
    from app.services.conversation_state import get_conversation

    # Check intake
    state = get_conversation(phone)
    logger.info(f"handle_intake_reply — state stage: {state['stage']} for {phone}")

    if state["stage"] == "in_progress":
        from app.services.intake_agent import process_intake_reply
        response = process_intake_reply(phone, message)
        send_whatsapp_message(phone, response)
        return

    # Check follow-up
    r = redis.from_url(app_settings.REDIS_URL, decode_responses=True)
    followup_data = r.get(f"followup:{phone}")

    if followup_data:
        data = json.loads(followup_data)
        from app.services.followup_agent import assess_followup_response

        result = assess_followup_response(
            patient_message=message,
            patient_name=data["patient_name"],
            doctor_name=data["doctor_name"],
            booking_id=data["booking_id"],
        )

        if result["crisis"]["crisis_detected"]:
            send_whatsapp_message(phone, result["crisis"]["response_text"])
            if result["crisis"]["severity"] == "high":
                r.delete(f"followup:{phone}")
                return

        response_msg = result["response_message"]

        if result["should_rebook"]:
            response_msg += (
                "\n\nWould you like to book a follow-up appointment? "
                "Open the Phila app to find your doctor. 📱"
            )

        send_whatsapp_message(phone, response_msg)
        r.delete(f"followup:{phone}")
        return

    # No active conversation
    logger.info(f"No active conversation for {phone} — sending generic response")
    send_whatsapp_message(
        phone,
        "Hi! Open the Phila app to book an appointment. "
        "Our assistant will reach out before your visit. 🏥"
    )