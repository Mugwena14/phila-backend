"""
Brevo (formerly Sendinblue) transactional email integration.

Used for document send via email channel. Free tier covers 300 emails/day -
plenty for the pilot. At scale (>9k emails/month) we move to a paid Brevo
tier or switch to AWS SES.

Env vars required at runtime:
  BREVO_API_KEY        - generated in Brevo dashboard, starts with xkeysib-
  BREVO_SENDER_EMAIL   - verified sender address (single email for demo,
                         verified domain for prod)
  BREVO_SENDER_NAME    - optional, displayed as the from name (default: Phila Health)

If BREVO_API_KEY is missing at send time, send_email_with_attachment returns
(False, useful_error) rather than crashing. The doctor sees a clear error,
not a 500.
"""
import os
import base64
import logging
from typing import Tuple

logger = logging.getLogger(__name__)


def send_email_with_attachment(
    to_email: str,
    to_name: str,
    subject: str,
    body_html: str,
    body_text: str,
    attachment_bytes: bytes,
    attachment_filename: str,
) -> Tuple[bool, str | None]:
    """
    Send a transactional email via Brevo with one attachment.
    Returns (success, error_message). On success, error_message is None.
    On failure, error_message is the exception text - logged to
    document_send_log for the audit trail.
    """
    api_key = os.environ.get("BREVO_API_KEY")
    if not api_key:
        return False, "BREVO_API_KEY not set on the server"

    sender_email = os.environ.get("BREVO_SENDER_EMAIL")
    if not sender_email:
        return False, "BREVO_SENDER_EMAIL not set on the server"

    sender_name = os.environ.get("BREVO_SENDER_NAME", "Phila Health")

    try:
        import sib_api_v3_sdk
        from sib_api_v3_sdk.rest import ApiException
    except ImportError:
        return False, "sib-api-v3-sdk not installed (did you pip install requirements.txt?)"

    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = api_key
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(configuration))

    encoded_attachment = base64.b64encode(attachment_bytes).decode("utf-8")

    email = sib_api_v3_sdk.SendSmtpEmail(
        sender={"name": sender_name, "email": sender_email},
        to=[{"email": to_email, "name": to_name or to_email}],
        subject=subject,
        html_content=body_html,
        text_content=body_text,
        attachment=[{
            "name": attachment_filename,
            "content": encoded_attachment,
        }],
    )

    try:
        result = api.send_transac_email(email)
        logger.info(f"Brevo email sent to {to_email} - messageId: {result.message_id}")
        return True, None
    except ApiException as e:
        err = f"Brevo API error: {e.status} {e.reason}"
        logger.error(f"{err} (to {to_email})")
        return False, err
    except Exception as e:
        err = f"Unexpected error sending email: {e}"
        logger.error(f"{err} (to {to_email})")
        return False, err


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    text_content: str,
    html_content: str | None = None,
) -> tuple[bool, str | None]:
    """
    Simpler send for transactional emails without attachments (e.g. OTPs).
    Mirrors send_email_with_attachment but skips the attachment plumbing.
    Returns (success, error_message).
    """
    import logging
    logger = logging.getLogger(__name__)

    if not BREVO_API_KEY:
        return False, "Brevo not configured"

    try:
        import sib_api_v3_sdk
        from sib_api_v3_sdk.rest import ApiException
    except ImportError:
        return False, "Brevo SDK not installed"

    cfg = sib_api_v3_sdk.Configuration()
    cfg.api_key["api-key"] = BREVO_API_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))

    payload = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email, "name": to_name or to_email}],
        sender={"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
        subject=subject,
        text_content=text_content,
        html_content=html_content,
    )

    try:
        api.send_transac_email(payload)
        logger.info(f"Brevo email sent to {to_email} - subject: {subject}")
        return True, None
    except ApiException as e:
        logger.error(f"Brevo API error: {e.status} {e.reason} (to {to_email})")
        return False, f"Brevo API error: {e.status} {e.reason}"
    except Exception as e:
        logger.error(f"Unexpected Brevo error: {e}")
        return False, f"Unexpected error: {e}"
