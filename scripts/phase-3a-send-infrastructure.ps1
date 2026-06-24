Write-Host "Phila Backend - Phase 3a - document send infrastructure (sandbox demo)" -ForegroundColor Cyan

# ── 1. requirements.txt - add mammoth + weasyprint ────────────────────────────
$reqContent = Get-Content requirements.txt -Raw
if ($reqContent -notmatch "mammoth") {
    Add-Content requirements.txt "`nmammoth>=1.6.0"
    Write-Host "  Added mammoth to requirements.txt" -ForegroundColor Green
}
if ($reqContent -notmatch "weasyprint") {
    Add-Content requirements.txt "weasyprint>=60.0"
    Write-Host "  Added weasyprint to requirements.txt" -ForegroundColor Green
}

# ── 2. nixpacks.toml - WeasyPrint needs pango on Railways Debian base ─────────
Set-Content "nixpacks.toml" @'
# WeasyPrint requires Pango, Cairo, and related rendering libraries at runtime.
# nixpacks default Python image doesn't include these. Adding via aptPkgs makes
# PDF rendering on Railway match what the local fallback would produce on a
# Linux dev box. If Pango is missing, the PDF service falls back to attaching
# raw .docx files - which still works on WhatsApp, just not as nice.
[phases.setup]
aptPkgs = ["libpango-1.0-0", "libpangoft2-1.0-0", "libcairo2", "libffi-dev"]
'@
Write-Host "  Created nixpacks.toml with WeasyPrint apt deps" -ForegroundColor Green

# ── 3. Migration ──────────────────────────────────────────────────────────────
Set-Content "alembic/versions/b2c8e1f4a5d6_add_document_send_tracking.py" @'
"""add document send tracking

Adds three nullable timestamp columns to patient_documents and creates the
document_send_log audit table. Every send attempt (success or failure) is
logged for POPIA compliance and debugging.

Revision ID: b2c8e1f4a5d6
Revises: 71116fd50a5f
Create Date: 2026-06-24

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b2c8e1f4a5d6"
down_revision: Union[str, Sequence[str], None] = "71116fd50a5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Send tracking - nullable so existing rows backfill cleanly
    op.add_column("patient_documents", sa.Column("sent_via_whatsapp_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("patient_documents", sa.Column("sent_via_email_at",    sa.DateTime(timezone=True), nullable=True))
    op.add_column("patient_documents", sa.Column("recalled_at",          sa.DateTime(timezone=True), nullable=True))

    # Audit log - every send attempt logged regardless of outcome
    op.create_table(
        "document_send_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("patient_documents.id"), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("initiated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_document_send_log_document_id", "document_send_log", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_send_log_document_id", table_name="document_send_log")
    op.drop_table("document_send_log")
    op.drop_column("patient_documents", "recalled_at")
    op.drop_column("patient_documents", "sent_via_email_at")
    op.drop_column("patient_documents", "sent_via_whatsapp_at")
'@
Write-Host "  Created migration b2c8e1f4a5d6_add_document_send_tracking.py" -ForegroundColor Green

# ── 4. patient_document.py - add the new columns to the model ─────────────────
Set-Content "app/models/patient_document.py" @'
from sqlalchemy import Column, String, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid
from app.db.database import Base


class PatientDocument(Base):
    __tablename__ = "patient_documents"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=True)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    doc_type = Column(String, nullable=False)
    # sick_letter | medical_certificate | referral_letter
    # visit_summary | template_{uuid}
    content = Column(Text, nullable=False)  # JSON string
    generated_by = Column(UUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Phase 3a - send tracking. Populated by POST /documents/{id}/send.
    # All three nullable; a doc that hasn't been sent on a channel just has the
    # column as NULL. Frontend reads these to render the Sent/Not-yet-sent pill.
    sent_via_whatsapp_at = Column(DateTime(timezone=True), nullable=True)
    sent_via_email_at = Column(DateTime(timezone=True), nullable=True)
    recalled_at = Column(DateTime(timezone=True), nullable=True)

    patient = relationship("User", foreign_keys=[patient_id])
    doctor = relationship("Doctor", foreign_keys=[doctor_id])
'@
Write-Host "  Updated app/models/patient_document.py with sent timestamps" -ForegroundColor Green

# ── 5. New model - document_send_log.py ───────────────────────────────────────
Set-Content "app/models/document_send_log.py" @'
"""
Audit log for every document send attempt.

POPIA-relevant: if a patient ever disputes whether they received a document
or claims it went to the wrong number, this table is the record. Every send
attempt - successful or failed - lands here with the recipient and timestamp.
Never delete rows from this table; retention requirement is at minimum the
same as patient medical records (6 years for adults in SA).
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.database import Base


class DocumentSendLog(Base):
    __tablename__ = "document_send_log"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("patient_documents.id"), nullable=False, index=True)
    channel = Column(String(32), nullable=False)       # 'whatsapp' | 'email'
    recipient = Column(String(256), nullable=False)    # phone (+27...) or email
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    initiated_by = Column(UUID(as_uuid=True), nullable=True)  # users.id of the doctor/receptionist
'@
Write-Host "  Created app/models/document_send_log.py" -ForegroundColor Green

# ── 6. Update __init__.py - add DocumentSendLog so autogenerate sees it ───────
Set-Content "app/models/__init__.py" @'
"""
Every SQLAlchemy model must be imported here.

Alembic's autogenerate compares Base.metadata against the database. Models
that aren't imported into the metadata at generation time are invisible -
autogenerate produces an empty upgrade() with `pass`. This was the root
cause of the favorites empty-migration on 23 June 2026.

When you add a new model, add a line here. No exceptions.
"""
from app.models.user import User
from app.models.doctor import Doctor
from app.models.booking import Booking
from app.models.slot import Slot
from app.models.working_hours import WorkingHours
from app.models.rating import Rating
from app.models.waitlist import Waitlist
from app.models.notification import Notification
from app.models.intake_brief import IntakeBrief
from app.models.patient_document import PatientDocument
from app.models.patient_health_summary import PatientHealthSummary
from app.models.patient_medication import PatientMedication
from app.models.patient_profile import PatientProfile
from app.models.document_template import DocumentTemplate
from app.models.favorite_doctor import FavoriteDoctor
from app.models.document_send_log import DocumentSendLog
'@
Write-Host "  Updated app/models/__init__.py to include DocumentSendLog" -ForegroundColor Green

# ── 7. Media token service - Redis-backed signed URLs ─────────────────────────
Set-Content "app/services/media_tokens.py" @'
"""
Short-lived signed-URL tokens for serving document PDFs to Twilio.

Twilio's media-send requires a public URL it can GET (server-to-server). We
don't want the PDF endpoint to be permanently public, so each send issues a
random 32-char token stored in Redis with a 15-minute TTL. The media
endpoint validates the token against the document_id before returning bytes.

Token lifecycle:
  - issue_token(doc_id)            ->  random token, stored in Redis (TTL 900s)
  - GET /documents/{id}/media/{tk} ->  validates and serves
  - 15 min later, token expires    ->  URL stops working

We deliberately don't single-use tokens: Twilio sometimes retries media
fetches, and strict single-use would race-condition with that. 15 minutes is
short enough that leaked URLs aren't a meaningful exposure window.
"""
import os
import secrets
import logging

logger = logging.getLogger(__name__)

TOKEN_TTL_SECONDS = 900   # 15 minutes
TOKEN_KEY_PREFIX = "media_token:"

_redis_client = None


def _get_redis():
    """Lazy redis client. REDIS_URL is set by Railway's Redis plugin."""
    global _redis_client
    if _redis_client is None:
        try:
            import redis
        except ImportError as e:
            raise RuntimeError("redis package not installed - cannot issue media tokens") from e
        redis_url = os.environ.get("REDIS_URL")
        if not redis_url:
            raise RuntimeError("REDIS_URL not set - cannot issue media tokens")
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


def issue_token(document_id: str) -> str:
    """Generate a token bound to a document_id, valid for TOKEN_TTL_SECONDS."""
    token = secrets.token_urlsafe(32)
    _get_redis().setex(f"{TOKEN_KEY_PREFIX}{token}", TOKEN_TTL_SECONDS, document_id)
    logger.info(f"Issued media token for doc {document_id[:8]} (TTL {TOKEN_TTL_SECONDS}s)")
    return token


def validate_token(token: str) -> str | None:
    """Return the document_id this token is bound to, or None if invalid/expired."""
    if not token:
        return None
    try:
        return _get_redis().get(f"{TOKEN_KEY_PREFIX}{token}")
    except Exception as e:
        logger.error(f"Redis lookup failed for token: {e}")
        return None
'@
Write-Host "  Created app/services/media_tokens.py" -ForegroundColor Green

# ── 8. PDF rendering service - WeasyPrint with graceful fallback ──────────────
Set-Content "app/services/document_pdf.py" @'
"""
Server-side PDF generation for documents being sent to patients.

Two paths:
  - Built-in docs (sick_letter, medical_certificate, referral_letter,
    visit_summary): rendered from JSON content via HTML template -> PDF.
  - Custom template docs (uploaded .docx with placeholder substitution):
    converted via mammoth (docx -> HTML) -> WeasyPrint (HTML -> PDF).

If WeasyPrint isn't available at runtime - either because the Python package
isn't installed (local Windows dev) or because Pango/Cairo system libs are
missing (some Linux deploys without the apt packages from nixpacks.toml) -
both paths fall back gracefully:
  - Built-in -> returns raw HTML as text/html (patient opens in browser)
  - Custom template -> returns the .docx unchanged (WhatsApp can open it)

This is the deliberate trade-off behind option (a) from the brainstorm:
free, in-process, fine for most doc types, occasional formatting loss on
complex Word layouts. Upgrade path is CloudConvert (or similar) - clean
drop-in replacement, same function signature.
"""
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
HTML_MIME = "text/html"


def _html_to_pdf_bytes(html: str) -> Optional[bytes]:
    """Render HTML to PDF using WeasyPrint. Returns None if WeasyPrint is unavailable."""
    try:
        import weasyprint
        return weasyprint.HTML(string=html).write_pdf()
    except ImportError:
        logger.warning("weasyprint not installed - PDF rendering unavailable")
        return None
    except OSError as e:
        # Missing system libs (pango, cairo). On Windows local dev this is normal.
        logger.warning(f"weasyprint system libs missing: {e} - falling back")
        return None
    except Exception as e:
        logger.error(f"weasyprint failed unexpectedly: {e}")
        return None


def render_template_pdf(docx_path: str) -> Tuple[bytes, str, str]:
    """
    Convert a generated .docx to PDF via mammoth -> WeasyPrint.

    Returns (file_bytes, mime_type, file_extension). On any failure, falls
    back to returning the raw .docx so the doctor's send action still
    succeeds - just with a less-friendly attachment.
    """
    try:
        import mammoth
        with open(docx_path, "rb") as f:
            result = mammoth.convert_to_html(f)
        html = f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; font-size: 12pt; line-height: 1.5; color: #111; padding: 40px; }}
h1, h2, h3 {{ color: #0F766E; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
td, th {{ border: 1px solid #ccc; padding: 6px 10px; }}
</style></head><body>{result.value}</body></html>
"""
        pdf = _html_to_pdf_bytes(html)
        if pdf is not None:
            return pdf, PDF_MIME, "pdf"
    except ImportError:
        logger.warning("mammoth not installed - skipping docx->html conversion")
    except Exception as e:
        logger.error(f"docx->pdf conversion failed: {e}")

    # Fallback: send the raw .docx
    logger.info(f"Falling back to .docx attachment for {docx_path}")
    with open(docx_path, "rb") as f:
        return f.read(), DOCX_MIME, "docx"


# ── Built-in doc HTML rendering ───────────────────────────────────────────────
# Mirrors the React DocumentPreview component in DocumentsPage.tsx.
# Keep visually in sync - both render the same data structure (the JSON saved
# under PatientDocument.content for built-in doc_types).

def _builtin_html(doc_type: str, content: dict, doctor_name: str, practice_name: str) -> str:
    """Render a built-in document as HTML, ready for WeasyPrint or browser display."""
    c = content
    title_map = {
        "sick_letter": "Sick Letter",
        "medical_certificate": "Medical Certificate",
        "referral_letter": "Referral Letter",
        "visit_summary": "Visit Summary",
    }
    title = title_map.get(doc_type, doc_type)
    practice = c.get("practice_name") or practice_name
    doctor = c.get("doctor_name") or doctor_name

    # Header block - same letterhead style across all doc types
    header = f"""
<div class="letterhead">
  <div class="practice">{practice}</div>
  <div class="doctor">{doctor}</div>
  {f'<div class="meta">{c["qualification"]}</div>' if c.get("qualification") else ""}
  {f'<div class="meta">HPCSA: {c["hpcsa_number"]}</div>' if c.get("hpcsa_number") else ""}
</div>
<div class="title">{title}</div>
<div class="date">Date: {c.get("date_issued", "")}</div>
"""

    body = ""
    if doc_type == "sick_letter":
        body = f"""
<p>To whom it may concern,</p>
<p>This is to certify that <strong>{c.get("patient_name", "")}</strong> was seen at this
practice on <strong>{c.get("date_of_visit", "")}</strong> and is unfit for work/school
for <strong>{c.get("days_off", "")}</strong> day(s){
  f', from {c["from_date"]}' + (f' to {c["to_date"]}' if c.get("to_date") else '')
  if c.get("from_date") else ''
}.</p>
{f'<p><strong>Reason:</strong> {c["diagnosis"]}</p>' if c.get("diagnosis") else ""}
{f'<p><strong>Notes:</strong> {c["notes"]}</p>' if c.get("notes") else ""}
<p>Signed,</p>
<p><strong>{doctor}</strong></p>
<p>{practice}</p>
"""
    elif doc_type == "medical_certificate":
        qual = f' ({c["qualification"]})' if c.get("qualification") else ""
        body = f"""
<p>I, <strong>{doctor}</strong>{qual}, hereby certify that I examined
<strong>{c.get("patient_name", "")}</strong> on <strong>{c.get("date_of_visit", "")}</strong>.</p>
{f'<p><strong>Diagnosis:</strong> {c["diagnosis"]}</p>' if c.get("diagnosis") else ""}
{f'<p><strong>Duration:</strong> {c["duration"]}</p>' if c.get("duration") else ""}
{f'<p><strong>Notes:</strong> {c["notes"]}</p>' if c.get("notes") else ""}
<p>Signed,</p>
<p><strong>{doctor}</strong></p>
{f'<p>HPCSA: {c["hpcsa_number"]}</p>' if c.get("hpcsa_number") else ""}
<p>{practice}</p>
"""
    elif doc_type == "referral_letter":
        addressee = (
            f'Dr {c["referred_to_doctor"]}' if c.get("referred_to_doctor")
            else f'{c.get("referred_to_specialty", "")} Specialist'
        )
        body = f"""
<p>Dear {addressee},</p>
<p>I am referring <strong>{c.get("patient_name", "")}</strong> to you for specialist assessment.</p>
{f'<p><strong>Reason for referral:</strong> {c["reason_for_referral"]}</p>' if c.get("reason_for_referral") else ""}
{f'<p><strong>Relevant history:</strong> {c["relevant_history"]}</p>' if c.get("relevant_history") else ""}
{f'<p><strong>Current medications:</strong> {c["current_medications"]}</p>' if c.get("current_medications") else ""}
{f'<p><strong>Allergies:</strong> {c["allergies"]}</p>' if c.get("allergies") else ""}
<p><strong>Urgency:</strong> {c.get("urgency", "Routine")}</p>
<p>Kind regards,</p>
<p><strong>{c.get("referring_doctor", doctor)}</strong></p>
<p>{practice}</p>
"""
    elif doc_type == "visit_summary":
        body = f"""
<p><strong>Patient:</strong> {c.get("patient_name", "")}</p>
<p><strong>Date:</strong> {c.get("date_of_visit", "")}</p>
<p><strong>Doctor:</strong> {doctor}</p>
{f'<p><strong>Chief complaint:</strong> {c["chief_complaint"]}</p>' if c.get("chief_complaint") else ""}
{f'<p><strong>Duration:</strong> {c["duration"]}</p>' if c.get("duration") else ""}
{f'<p><strong>Severity:</strong> {c["severity"]}/10</p>' if c.get("severity") else ""}
{f'<p><strong>Medications:</strong> {c["medications_prescribed"]}</p>' if c.get("medications_prescribed") else ""}
{f'<p><strong>Allergies:</strong> {c["allergies"]}</p>' if c.get("allergies") else ""}
{f'<p><strong>Recommendations:</strong> {c["recommendations"]}</p>' if c.get("recommendations") else ""}
{f'<p><strong>Follow-up:</strong> {c["follow_up"]}</p>' if c.get("follow_up") else ""}
{f'<p><strong>Notes:</strong> {c["notes"]}</p>' if c.get("notes") else ""}
<p><strong>{doctor}</strong></p>
<p>{practice}</p>
"""
    else:
        body = f"<p>Document type: {doc_type}</p>"

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @page {{ size: A4; margin: 25mm; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-size: 11pt; line-height: 1.7; color: #111; }}
  .letterhead {{ border-bottom: 2px solid #0F766E; padding-bottom: 12px; margin-bottom: 24px; }}
  .practice {{ font-size: 18pt; font-weight: 700; color: #0F766E; }}
  .doctor {{ font-size: 11pt; color: #555; }}
  .meta {{ font-size: 10pt; color: #555; }}
  .title {{ font-size: 14pt; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 16px; }}
  .date {{ font-size: 10pt; color: #666; margin-bottom: 20px; }}
  p {{ margin: 8px 0; }}
  strong {{ font-weight: 600; }}
</style>
</head>
<body>
{header}
{body}
</body>
</html>
"""


def render_builtin_pdf(doc_type: str, content: dict, doctor_name: str, practice_name: str) -> Tuple[bytes, str, str]:
    """
    Render a built-in doc as PDF. Falls back to HTML if WeasyPrint is unavailable.
    Returns (file_bytes, mime_type, file_extension).
    """
    html = _builtin_html(doc_type, content, doctor_name, practice_name)
    pdf = _html_to_pdf_bytes(html)
    if pdf is not None:
        return pdf, PDF_MIME, "pdf"
    logger.info(f"Falling back to HTML for built-in doc ({doc_type})")
    return html.encode("utf-8"), HTML_MIME, "html"
'@
Write-Host "  Created app/services/document_pdf.py" -ForegroundColor Green

# ── 9. Extend whatsapp.py with media send ─────────────────────────────────────
Set-Content "app/services/whatsapp.py" @'
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
    Send a WhatsApp message with a media attachment (PDF, DOCX, etc.).
    Twilio fetches media_url server-to-server, so it must be publicly reachable.

    Returns (success, error_message). On success, error_message is None.
    On failure, error_message is the exception text - logged to
    document_send_log for the audit trail.

    SANDBOX NOTE: in sandbox mode, the recipient must have first joined the
    sandbox by texting the join code to the sandbox number. In prod with an
    approved sender, this restriction goes away.
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
'@
Write-Host "  Updated app/services/whatsapp.py with send_whatsapp_with_media" -ForegroundColor Green

# ── 10. Routes - add send endpoint + media endpoint, include sent timestamps ──
Set-Content "app/api/routes/documents.py" @'
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response as FastAPIResponse
from sqlalchemy.orm import Session
from uuid import UUID
from pydantic import BaseModel
from datetime import datetime, timezone
import json
import uuid
import os
import shutil
import logging

from app.db.database import get_db
from app.models.patient_document import PatientDocument
from app.models.document_template import DocumentTemplate
from app.models.document_send_log import DocumentSendLog
from app.models.user import User
from app.models.doctor import Doctor
from app.models.booking import Booking
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.services.template_starters import create_starter_template
from app.services.document_templates import (
    extract_placeholders,
    fill_placeholders,
    build_sample_values,
    TemplateError,
)
from app.services.media_tokens import issue_token, validate_token
from app.services.document_pdf import (
    render_builtin_pdf,
    render_template_pdf,
)
from app.services.whatsapp import send_whatsapp_with_media

router = APIRouter(prefix="/documents", tags=["documents"])
security = HTTPBearer()
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads/templates"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Base URL Twilio uses to fetch document media. Set via env var on Railway;
# falls back to the known prod URL so the demo can run before the var is set.
PUBLIC_BASE_URL = os.environ.get(
    "PUBLIC_BASE_URL",
    "https://phila-backend-production.up.railway.app",
).rstrip("/")

DOC_TYPE_LABELS = {
    "sick_letter":         "sick note",
    "medical_certificate": "medical certificate",
    "referral_letter":     "referral letter",
    "visit_summary":       "visit summary",
}


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _get_doctor(db: Session, current_user: User) -> Doctor:
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")
    return doctor


def _serialize_doc(d: PatientDocument) -> dict:
    """Common serialization used by every doc endpoint."""
    return {
        "id": str(d.id),
        "patient_id": str(d.patient_id),
        "booking_id": str(d.booking_id) if d.booking_id else None,
        "doc_type": d.doc_type,
        "content": json.loads(d.content),
        "created_at": str(d.created_at),
        "sent_via_whatsapp_at": d.sent_via_whatsapp_at.isoformat() if d.sent_via_whatsapp_at else None,
        "sent_via_email_at": d.sent_via_email_at.isoformat() if d.sent_via_email_at else None,
        "recalled_at": d.recalled_at.isoformat() if d.recalled_at else None,
    }


# ── TEMPLATE ENDPOINTS ────────────────────────────────────────────────────────

@router.post("/templates/upload", status_code=201)
async def upload_template(
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doctor = _get_doctor(db, current_user)
    if not file.filename or not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    template_id = str(uuid.uuid4())
    safe_filename = f"{template_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        placeholders = extract_placeholders(file_path)
    except TemplateError as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail=str(e))

    if not placeholders:
        logger.warning("Template '%s' uploaded by doctor %s contains 0 placeholders", name, doctor.id)

    template = DocumentTemplate(
        id=uuid.UUID(template_id),
        doctor_id=doctor.id,
        name=name,
        description=description,
        filename=file.filename,
        file_path=file_path,
        placeholders=json.dumps(placeholders),
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    return {
        "id": str(template.id),
        "name": template.name,
        "description": template.description,
        "filename": template.filename,
        "placeholders": placeholders,
        "created_at": str(template.created_at),
    }


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doctor = _get_doctor(db, current_user)
    templates = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.doctor_id == doctor.id)
        .order_by(DocumentTemplate.created_at.desc())
        .all()
    )
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "filename": t.filename,
            "placeholders": json.loads(t.placeholders),
            "created_at": str(t.created_at),
        }
        for t in templates
    ]


@router.delete("/templates/{template_id}", status_code=200)
def delete_template(template_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doctor = _get_doctor(db, current_user)
    template = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == template_id, DocumentTemplate.doctor_id == doctor.id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    if template.file_path and os.path.exists(template.file_path):
        os.remove(template.file_path)
    db.delete(template)
    db.commit()
    return {"message": "Template deleted"}


@router.post("/templates/{template_id}/preview")
def preview_template(template_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doctor = _get_doctor(db, current_user)
    template = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == template_id, DocumentTemplate.doctor_id == doctor.id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    placeholders = json.loads(template.placeholders)
    sample_values = build_sample_values(placeholders)
    preview_filename = f"preview_{uuid.uuid4()}.docx"
    preview_path = os.path.join(UPLOAD_DIR, preview_filename)

    try:
        fill_placeholders(template.file_path, sample_values, preview_path)
    except TemplateError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return FileResponse(
        path=preview_path,
        filename=f"{template.name}_PREVIEW.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@router.post("/templates/{template_id}/generate")
def generate_from_template(
    template_id: UUID,
    booking_id: UUID,
    values: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doctor = _get_doctor(db, current_user)
    template = (
        db.query(DocumentTemplate)
        .filter(DocumentTemplate.id == template_id, DocumentTemplate.doctor_id == doctor.id)
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    output_filename = f"filled_{uuid.uuid4()}.docx"
    output_path = os.path.join(UPLOAD_DIR, output_filename)

    try:
        fill_placeholders(template.file_path, values, output_path)
    except TemplateError as e:
        raise HTTPException(status_code=500, detail=str(e))

    doc = PatientDocument(
        id=uuid.uuid4(),
        patient_id=booking.patient_id,
        booking_id=booking.id,
        doctor_id=doctor.id,
        doc_type=f"template_{template_id}",
        content=json.dumps({
            "_template_id": str(template_id),
            "_template_name": template.name,
            "_output_file": output_path,
            **values,
        }),
        generated_by=current_user.id,
    )
    db.add(doc)
    db.commit()

    return FileResponse(
        path=output_path,
        filename=f"{template.name}_{booking_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# ── BUILT-IN DOC ENDPOINTS ────────────────────────────────────────────────────

class GenerateDocumentRequest(BaseModel):
    booking_id: UUID
    doc_type: str
    content: dict


@router.post("/generate", status_code=201)
def generate_document(data: GenerateDocumentRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doctor = _get_doctor(db, current_user)
    booking = db.query(Booking).filter(Booking.id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    doc = PatientDocument(
        id=uuid.uuid4(),
        patient_id=booking.patient_id,
        booking_id=booking.id,
        doctor_id=doctor.id,
        doc_type=data.doc_type,
        content=json.dumps(data.content),
        generated_by=current_user.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    return {
        **_serialize_doc(doc),
        "message": "Document generated successfully",
    }


@router.get("/patient/{patient_id}")
def get_patient_documents(patient_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    docs = (
        db.query(PatientDocument)
        .filter(PatientDocument.patient_id == patient_id)
        .order_by(PatientDocument.created_at.desc())
        .all()
    )
    return [_serialize_doc(d) for d in docs]


# ── SEND ENDPOINT - the Phase 3a core ─────────────────────────────────────────

class SendDocumentRequest(BaseModel):
    channel: str  # 'whatsapp' | 'email' (email is Phase 3b)


@router.post("/{doc_id}/send")
def send_document(
    doc_id: UUID,
    data: SendDocumentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send a generated document to the patient via the specified channel.
    Issues a 15-minute signed media URL, calls Twilio, logs the outcome.
    """
    if data.channel not in ("whatsapp", "email"):
        raise HTTPException(status_code=400, detail="Channel must be 'whatsapp' or 'email'")
    if data.channel == "email":
        raise HTTPException(status_code=501, detail="Email send is coming in Phase 3b")

    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorisation - only the doctor who owns this doc can send it
    doctor = _get_doctor(db, current_user)
    if doc.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Not your document")

    patient = db.query(User).filter(User.id == doc.patient_id).first()
    if not patient or not patient.phone:
        raise HTTPException(status_code=400, detail="Patient phone number not available")

    # Issue signed URL Twilio can fetch
    token = issue_token(str(doc.id))
    media_url = f"{PUBLIC_BASE_URL}/api/v1/documents/{doc.id}/media/{token}"

    # Build the message body
    doc_label = DOC_TYPE_LABELS.get(doc.doc_type, "document")
    if doc.doc_type.startswith("template_"):
        try:
            content = json.loads(doc.content)
            doc_label = content.get("_template_name", "document")
        except Exception:
            pass
    practice_name = doctor.practice_name or "your doctor"
    body = f"Hi, your {doc_label} from {practice_name} is ready. View attached."

    # Send
    success, error = send_whatsapp_with_media(patient.phone, body, media_url)

    # Audit log - always written, success or fail
    log_entry = DocumentSendLog(
        id=uuid.uuid4(),
        document_id=doc.id,
        channel=data.channel,
        recipient=patient.phone,
        success=success,
        error_message=error,
        initiated_by=current_user.id,
    )
    db.add(log_entry)

    if success:
        doc.sent_via_whatsapp_at = datetime.now(timezone.utc)

    db.commit()

    if not success:
        raise HTTPException(status_code=502, detail=f"Send failed: {error}")

    return {
        "success": True,
        "channel": data.channel,
        "sent_at": doc.sent_via_whatsapp_at.isoformat(),
    }


# ── MEDIA ENDPOINT - public, token-gated ──────────────────────────────────────

@router.get("/{doc_id}/media/{token}")
def get_document_media(doc_id: UUID, token: str, db: Session = Depends(get_db)):
    """
    Public endpoint - Twilio fetches the PDF here, server-to-server, using the
    signed token issued by /send. No auth header; the URL token IS the auth.
    Tokens expire after 15 minutes.
    """
    bound_doc_id = validate_token(token)
    if not bound_doc_id or bound_doc_id != str(doc_id):
        raise HTTPException(status_code=404, detail="Not found")

    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    content = json.loads(doc.content)

    # Resolve doctor name and practice for the letterhead
    doctor = db.query(Doctor).filter(Doctor.id == doc.doctor_id).first()
    practice_name = doctor.practice_name if doctor else ""
    doctor_name = ""
    if doctor:
        doctor_user = db.query(User).filter(User.id == doctor.user_id).first()
        if doctor_user and doctor_user.full_name:
            doctor_name = f"Dr. {doctor_user.full_name}"

    if doc.doc_type.startswith("template_") and "_output_file" in content:
        file_bytes, mime_type, ext = render_template_pdf(content["_output_file"])
    else:
        file_bytes, mime_type, ext = render_builtin_pdf(doc.doc_type, content, doctor_name, practice_name)

    filename = f"{doc.doc_type}_{str(doc.id)[:8]}.{ext}"
    return FastAPIResponse(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ── GET BY ID - kept last so /{doc_id}/send and /{doc_id}/media/{tk} match first ─

@router.get("/{doc_id}")
def get_document(doc_id: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _serialize_doc(doc)


@router.get("/templates/starter/{doc_type}")
def download_starter_template(doc_type: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    valid_types = ["sick_letter", "medical_certificate", "referral_letter", "visit_summary"]
    if doc_type not in valid_types:
        raise HTTPException(status_code=400, detail="Invalid doc type")

    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    practice_name = doctor.practice_name if doctor else "Your Practice Name"
    user = db.query(User).filter(User.id == current_user.id).first()
    doctor_name = f"Dr. {user.full_name}" if user else "Dr. Your Name"

    docx_bytes = create_starter_template(doc_type, practice_name, doctor_name)
    filename_map = {
        "sick_letter": "Sick_Letter_Starter.docx",
        "medical_certificate": "Medical_Certificate_Starter.docx",
        "referral_letter": "Referral_Letter_Starter.docx",
        "visit_summary": "Visit_Summary_Starter.docx",
    }
    return FastAPIResponse(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename_map[doc_type]}"'},
    )
'@
Write-Host "  Updated app/api/routes/documents.py - added /send and /media/{token} endpoints" -ForegroundColor Green

git add .
git commit -m "Phase 3a - document send infrastructure (sandbox demo). Migration b2c8e1f4a5d6 adds sent_via_whatsapp_at / sent_via_email_at / recalled_at to patient_documents plus document_send_log audit table. New PDF service uses WeasyPrint with graceful fallback to HTML / .docx when system libs are missing. Token-gated public /media/{token} endpoint serves PDFs to Twilio with 15-minute TTL via Redis. Extended whatsapp service with media-send. New POST /documents/{id}/send endpoint orchestrates - issues token, builds media URL, sends, logs to audit table, updates sent timestamps. Patient doc GET endpoints now include sent timestamps so frontend pill state reflects reality. requirements.txt + nixpacks.toml updated for WeasyPrint deps."
Write-Host "Phase 3a backend committed locally. Order of operations below." -ForegroundColor Yellow