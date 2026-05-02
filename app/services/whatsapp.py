from twilio.rest import Client
from twilio.base.exceptions import TwilioException
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# Initialise Twilio client 
client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def format_whatsapp_number(phone: str) -> str:
    # Strip all spaces and dashes
    cleaned = phone.replace(" ", "").replace("-", "")

    # Convert SA local format to international
    if cleaned.startswith("0"):
        cleaned = "+27" + cleaned[1:]
    elif cleaned.startswith("27") and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    elif not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    return f"whatsapp:{cleaned}"


def send_whatsapp_message(to_phone: str, message: str) -> bool:
    """
    Send a WhatsApp message via Twilio.
    Returns True if sent successfully, False if failed.
    """
    try:
        to_formatted = format_whatsapp_number(to_phone)

        msg = client.messages.create(
            from_=settings.TWILIO_WHATSAPP_FROM,
            to=to_formatted,
            body=message,
        )

        logger.info(f"WhatsApp sent to {to_formatted} — SID: {msg.sid}")
        return True

    except TwilioException as e:
        logger.error(f"Twilio error sending to {to_phone}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending WhatsApp to {to_phone}: {e}")
        return False

def parse_incoming_message(form_data: dict) -> dict:
    raw_from = form_data.get("From", "").replace("whatsapp:", "").strip()

    return {
        "from_number": raw_from,  # keep as +27XXXXXXXXX
        "to_number": form_data.get("To", "").replace("whatsapp:", ""),
        "body": form_data.get("Body", "").strip(),
        "message_sid": form_data.get("MessageSid", ""),
        "account_sid": form_data.get("AccountSid", ""),
        "num_media": int(form_data.get("NumMedia", 0)),
    }