from fastapi import APIRouter, Request, Response
from app.services.whatsapp import parse_incoming_message, send_whatsapp_message
import logging

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger(__name__)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Twilio sends ALL incoming WhatsApp replies here.
    This is the central router — it decides what to do
    based on the message content and sender.
    """
    # Twilio sends form-encoded data — parse it
    form_data = await request.form()
    data = parse_incoming_message(dict(form_data))

    from_number = data["from_number"]
    body = data["body"].lower().strip()
    original_body = data["body"].strip()

    logger.info(f"Incoming WhatsApp from {from_number}: {original_body}")

    # Route based on message content
    # Week 4 — we handle YES/NO for no-show prevention
    if body in ["yes", "y", "confirm", "yes ✓"]:
        await handle_confirmation(from_number)

    elif body in ["no", "n", "cancel"]:
        await handle_cancellation(from_number)

    else:
        # All other messages — route to intake agent
        # We'll build this in the next step
        await handle_intake_reply(from_number, original_body)

    # Twilio expects a 200 response with empty TwiML
    # If we return anything else, Twilio retries — don't do that
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
    from datetime import date, datetime

    db = SessionLocal()
    try:
        # Find user by phone
        user = db.query(User).filter(User.phone == phone).first()
        if not user:
            logger.warning(f"No user found for phone {phone}")
            return

        # Find their next upcoming unconfirmed booking
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
            # Cancel booking + release slot
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

            # Notify first person on waitlist
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
    Route to intake agent — we'll build this fully in the next step.
    For now just echo back so we can test the webhook is working.
    """
    logger.info(f"Intake reply from {phone}: {message}")
    # Placeholder — intake agent wired in next step
    send_whatsapp_message(
        phone,
        f"Got your message: '{message}'. Our assistant will respond shortly."
    )