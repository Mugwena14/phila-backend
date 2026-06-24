from twilio.rest import Client
from twilio.base.exceptions import TwilioException
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def format_whatsapp_number(phone: str) -> str:
    cleaned = phone.replace(" ", "").replace("-", "")
    if cleaned.startswith("0"):
        cleaned = "+27" + cleaned[1:]
    elif cleaned.startswith("27") and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return f"whatsapp:{cleaned}"


def send_whatsapp_message(to_phone: str, message: str) -> bool:
    """Send a text-only WhatsApp message. Returns True on success."""
    try:
        to_formatted = format_whatsapp_number(to_phone)
        msg = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=to_formatted,
            body=message,
        )
        logger.info(f"WhatsApp sent to {to_formatted} - SID: {msg.sid}")
        return True
    except TwilioException as e:
        logger.error(f"Twilio error sending to {to_phone}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending WhatsApp to {to_phone}: {e}")
        return False


def send_whatsapp_with_media(to_phone: str, body: str, media_url: str) -> tuple[bool, str | None]:
    """
    Send a WhatsApp message with a media attachment. Twilio fetches media_url
    server-to-server, so it must be publicly reachable.

    Returns (success, error_message).
    """
    try:
        to_formatted = format_whatsapp_number(to_phone)
        msg = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=to_formatted,
            body=body,
            media_url=[media_url],
        )
        logger.info(f"WhatsApp media sent to {to_formatted} - SID: {msg.sid} - URL: {media_url[:80]}")
        return True, None
    except TwilioException as e:
        err = f"Twilio error: {e}"
        logger.error(f"{err} (to {to_phone})")
        return False, err
    except Exception as e:
        err = f"Unexpected error: {e}"
        logger.error(f"{err} (to {to_phone})")
        return False, err


def send_recall_message(to_phone: str, doc_label: str, practice_name: str) -> tuple[bool, str | None]:
    """
    Send a text WhatsApp telling the patient to disregard a previously-sent doc.
    Used when a doctor realises a sent doc was wrong.
    Returns (success, error_message).
    """
    body = (
        f"Hi, please disregard the previous {doc_label} from {practice_name}. "
        f"It contained an error. A corrected version will follow shortly if applicable."
    )
    try:
        to_formatted = format_whatsapp_number(to_phone)
        msg = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=to_formatted,
            body=body,
        )
        logger.info(f"Recall message sent to {to_formatted} - SID: {msg.sid}")
        return True, None
    except TwilioException as e:
        err = f"Twilio error: {e}"
        logger.error(f"{err} (to {to_phone})")
        return False, err
    except Exception as e:
        err = f"Unexpected error: {e}"
        logger.error(f"{err} (to {to_phone})")
        return False, err


def parse_incoming_message(form_data: dict) -> dict:
    raw_from = form_data.get("From", "").replace("whatsapp:", "").strip()
    return {
        "from_number": raw_from,
        "to_number": form_data.get("To", "").replace("whatsapp:", ""),
        "body": form_data.get("Body", "").strip(),
        "message_sid": form_data.get("MessageSid", ""),
        "account_sid": form_data.get("AccountSid", ""),
        "num_media": int(form_data.get("NumMedia", 0)),
    }
