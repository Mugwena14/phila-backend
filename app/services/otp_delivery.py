"""
Orchestrates OTP delivery across email / WhatsApp / SMS channels.

The OTP service (app.services.otp) generates and stores codes; this
module delivers them. Kept separate so the OTP storage logic stays
channel-agnostic.
"""
import logging
from app.services.otp import issue_code

logger = logging.getLogger(__name__)


def _email_body(code: str, app_name: str = "Phila") -> str:
    """Plain-text fallback. The Brevo send function also takes html_content
    if we want pretty formatting - skipping for pilot, plain text is fine."""
    return (
        f"Your {app_name} verification code is: {code}\n\n"
        f"This code expires in 5 minutes. If you didn't request this, "
        f"you can safely ignore this email."
    )


def _whatsapp_body(code: str) -> str:
    return (
        f"Your Phila verification code is: *{code}*\n\n"
        f"This code expires in 5 minutes."
    )


def _sms_body(code: str) -> str:
    """SMS - keep short, segments are charged per 160 chars."""
    return f"Phila code: {code}. Expires in 5 min."


def send_otp_via_email(email: str) -> tuple[bool, str | None]:
    """Generate code, store in Redis, send via Brevo. Returns (success, error)."""
    from app.services.email_brevo import send_email_with_attachment, send_email

    code = issue_code("email", email)
    body = _email_body(code)

    # Brevo's send_email is a simpler version without attachment
    # If your email_brevo.py only has send_email_with_attachment, we use
    # that with attachment=None
    try:
        success, error = send_email(
            to_email=email,
            to_name="",
            subject="Your Phila verification code",
            text_content=body,
        )
        return success, error
    except (ImportError, AttributeError):
        # email_brevo.py only has the attachment version
        success, error = send_email_with_attachment(
            to_email=email,
            to_name="",
            subject="Your Phila verification code",
            text_content=body,
            attachment_bytes=None,
            attachment_filename=None,
        )
        return success, error


def send_otp_via_whatsapp(phone: str) -> tuple[bool, str | None]:
    """Generate code, store in Redis, send via Twilio WhatsApp. Returns (success, error).

    This will fail in the Twilio sandbox unless the user has already joined
    the sandbox - by design. Outside-sandbox-via-business-sender will need
    Twilio's approved business WhatsApp sender, which is the path forward
    when WhatsApp OTP is enabled for real."""
    from app.services.whatsapp import send_whatsapp_message

    code = issue_code("whatsapp", phone)
    body = _whatsapp_body(code)

    try:
        success = send_whatsapp_message(phone, body)
        if success:
            return True, None
        return False, "WhatsApp send failed - patient may not be reachable on WhatsApp right now"
    except Exception as e:
        logger.error(f"WhatsApp OTP send to {phone} crashed: {e}")
        return False, f"Unexpected error: {e}"


def send_otp_via_sms(phone: str) -> tuple[bool, str | None]:
    """Generate code, store in Redis, send via Twilio SMS. Returns (success, error).
    Will gracefully fail with 'SMS not configured' until TWILIO_SMS_FROM is set."""
    from app.services.sms import send_sms, is_sms_enabled

    if not is_sms_enabled():
        return False, "SMS verification isn't available yet. Please use email or WhatsApp."

    code = issue_code("sms", phone)
    body = _sms_body(code)
    return send_sms(phone, body)