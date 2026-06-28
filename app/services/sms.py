"""
Twilio SMS send. Written but gated by TWILIO_SMS_FROM env var which is
intentionally unset in pilot - the function early-returns and the OTP
service treats SMS as unavailable.

When you set TWILIO_SMS_FROM to a real SMS-capable Twilio number, this
service starts working with no code change.

Cost note: SA SMS via Twilio is roughly R0.40 per send. At 100 patients/
month with ~1 OTP each that's R40/mo. Watch for retry storms - rate limit
is in place at the OTP layer but worth tracking.
"""
import os
import logging

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM")  # e.g. "+12025551234"


def is_sms_enabled() -> bool:
    """SMS is enabled iff TWILIO_SMS_FROM is set. Allows the OTP service
    to gracefully skip SMS as a channel option without raising."""
    return bool(TWILIO_SMS_FROM)


def send_sms(to_phone: str, body: str) -> tuple[bool, str | None]:
    """
    Send an SMS via Twilio. Returns (success, error_message).
    If TWILIO_SMS_FROM is unset, returns (False, "SMS not configured")
    without contacting Twilio.
    """
    if not is_sms_enabled():
        return False, "SMS not configured for this environment"

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return False, "Twilio credentials not configured"

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_SMS_FROM,
            to=to_phone,
            body=body,
        )
        logger.info(f"SMS sent to {to_phone} - SID: {message.sid}")
        return True, None
    except TwilioRestException as e:
        logger.error(f"Twilio SMS error: {e.code} - {e.msg}")
        return False, f"Twilio error: {e.msg}"
    except Exception as e:
        logger.error(f"Unexpected SMS send error: {e}")
        return False, f"Unexpected error: {e}"