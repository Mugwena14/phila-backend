Write-Host "Phila Backend - Phase 3b - recall, email via Brevo, doc list send" -ForegroundColor Cyan

# 1. requirements - add the Brevo SDK
$req = Get-Content requirements.txt -Raw
if ($req -notmatch "sib-api-v3-sdk") {
    Add-Content requirements.txt "`nsib-api-v3-sdk>=7.6.0"
    Write-Host "  Added sib-api-v3-sdk to requirements.txt (Brevo Python SDK)" -ForegroundColor Green
}

# 2. New service - Brevo transactional email
Set-Content "app/services/email_brevo.py" @'
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
'@
Write-Host "  Created app/services/email_brevo.py" -ForegroundColor Green

# 3. Extend whatsapp service with recall message
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
'@
Write-Host "  Updated app/services/whatsapp.py with send_recall_message" -ForegroundColor Green

# 4. Extend the documents route - email channel, recall endpoint
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
from app.services.whatsapp import send_whatsapp_with_media, send_recall_message
from app.services.email_brevo import send_email_with_attachment

router = APIRouter(prefix="/documents", tags=["documents"])
security = HTTPBearer()
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads/templates"
os.makedirs(UPLOAD_DIR, exist_ok=True)

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


def _doc_label(doc: PatientDocument) -> str:
    """Human-readable label for a doc - used in messages and email subjects."""
    if doc.doc_type.startswith("template_"):
        try:
            content = json.loads(doc.content)
            return content.get("_template_name", "document")
        except Exception:
            return "document"
    return DOC_TYPE_LABELS.get(doc.doc_type, "document")


def _render_doc_bytes(doc: PatientDocument, doctor: Doctor, doctor_user: User | None) -> tuple[bytes, str, str]:
    """Render this doc to bytes via the PDF service. Returns (bytes, mime, ext)."""
    content = json.loads(doc.content)
    practice_name = doctor.practice_name if doctor else ""
    doctor_name = ""
    if doctor_user and doctor_user.full_name:
        doctor_name = f"Dr. {doctor_user.full_name}"

    if doc.doc_type.startswith("template_") and "_output_file" in content:
        return render_template_pdf(content["_output_file"])
    return render_builtin_pdf(doc.doc_type, content, doctor_name, practice_name)


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

    doc_id = uuid.uuid4()
    doc = PatientDocument(
        id=doc_id,
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
        headers={"X-Document-Id": str(doc_id)},  # Frontend reads this to wire Send buttons
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


# ── SEND ENDPOINT - WhatsApp + Email ──────────────────────────────────────────

class SendDocumentRequest(BaseModel):
    channel: str  # 'whatsapp' | 'email'


@router.post("/{doc_id}/send")
def send_document(
    doc_id: UUID,
    data: SendDocumentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send a generated document to the patient via WhatsApp or Email.
    Issues a 15-minute signed media URL for WhatsApp, renders bytes inline
    for email attachment, logs every attempt to document_send_log.
    """
    if data.channel not in ("whatsapp", "email"):
        raise HTTPException(status_code=400, detail="Channel must be 'whatsapp' or 'email'")

    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doctor = _get_doctor(db, current_user)
    if doc.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Not your document")

    patient = db.query(User).filter(User.id == doc.patient_id).first()
    if not patient:
        raise HTTPException(status_code=400, detail="Patient not found")

    doctor_user = db.query(User).filter(User.id == doctor.user_id).first()
    practice_name = doctor.practice_name or "your doctor"
    doc_label = _doc_label(doc)

    success = False
    error: str | None = None
    recipient: str = ""

    if data.channel == "whatsapp":
        if not patient.phone:
            raise HTTPException(status_code=400, detail="Patient phone number not available")
        recipient = patient.phone

        token = issue_token(str(doc.id))
        media_url = f"{PUBLIC_BASE_URL}/api/v1/documents/{doc.id}/media/{token}"
        body = f"Hi, your {doc_label} from {practice_name} is ready. View attached."
        success, error = send_whatsapp_with_media(patient.phone, body, media_url)
        if success:
            doc.sent_via_whatsapp_at = datetime.now(timezone.utc)

    elif data.channel == "email":
        if not patient.email:
            raise HTTPException(status_code=400, detail="Patient email not available")
        recipient = patient.email

        # Render bytes inline - email needs the actual file, not a URL
        file_bytes, mime_type, ext = _render_doc_bytes(doc, doctor, doctor_user)
        attachment_filename = f"{doc_label.replace(' ', '_')}.{ext}"

        subject = f"Your {doc_label} from {practice_name}"
        body_html = f"""
<p>Hi {patient.full_name or 'there'},</p>
<p>Your {doc_label} from <strong>{practice_name}</strong> is attached to this email.</p>
<p>If you have questions about the contents, please contact the practice directly.</p>
<p>- Phila Health</p>
"""
        body_text = (
            f"Hi {patient.full_name or 'there'},\n\n"
            f"Your {doc_label} from {practice_name} is attached.\n\n"
            f"If you have questions about the contents, please contact the practice directly.\n\n"
            f"- Phila Health"
        )
        success, error = send_email_with_attachment(
            to_email=patient.email,
            to_name=patient.full_name or "",
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            attachment_bytes=file_bytes,
            attachment_filename=attachment_filename,
        )
        if success:
            doc.sent_via_email_at = datetime.now(timezone.utc)

    log_entry = DocumentSendLog(
        id=uuid.uuid4(),
        document_id=doc.id,
        channel=data.channel,
        recipient=recipient,
        success=success,
        error_message=error,
        initiated_by=current_user.id,
    )
    db.add(log_entry)
    db.commit()

    if not success:
        raise HTTPException(status_code=502, detail=f"Send failed: {error}")

    return {
        "success": True,
        "channel": data.channel,
        "sent_at": (doc.sent_via_whatsapp_at if data.channel == "whatsapp" else doc.sent_via_email_at).isoformat(),
    }


# ── RECALL ENDPOINT ───────────────────────────────────────────────────────────

@router.post("/{doc_id}/recall")
def recall_document(
    doc_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Mark a sent document as recalled. Sends a WhatsApp follow-up if the doc
    was sent via WhatsApp. Stamps recalled_at on the doc. Logs to audit.

    Recall is one-way - you cant un-recall. The intended workflow is:
    realize the doc was wrong, recall it (patient gets disregard message),
    generate a corrected version, send that.
    """
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doctor = _get_doctor(db, current_user)
    if doc.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Not your document")

    if doc.recalled_at:
        raise HTTPException(status_code=400, detail="Document already recalled")

    if not doc.sent_via_whatsapp_at and not doc.sent_via_email_at:
        raise HTTPException(status_code=400, detail="Document hasn't been sent - nothing to recall")

    patient = db.query(User).filter(User.id == doc.patient_id).first()
    practice_name = doctor.practice_name or "your doctor"
    doc_label = _doc_label(doc)

    # Send a WhatsApp disregard message if the doc went via WhatsApp.
    # Email recall is intentionally NOT auto-sent here - email is harder to
    # un-receive than a WhatsApp message in a noisy chat, and an auto-recall
    # email can alarm the patient. The doctor can email manually if needed.
    recall_success = True
    recall_error = None
    if doc.sent_via_whatsapp_at and patient and patient.phone:
        recall_success, recall_error = send_recall_message(patient.phone, doc_label, practice_name)

    doc.recalled_at = datetime.now(timezone.utc)

    log_entry = DocumentSendLog(
        id=uuid.uuid4(),
        document_id=doc.id,
        channel="recall",
        recipient=patient.phone if patient and patient.phone else "",
        success=recall_success,
        error_message=recall_error,
        initiated_by=current_user.id,
    )
    db.add(log_entry)
    db.commit()

    return {
        "success": True,
        "recalled_at": doc.recalled_at.isoformat(),
        "recall_message_sent": recall_success,
    }


# ── MEDIA ENDPOINT - public, token-gated ──────────────────────────────────────

@router.get("/{doc_id}/media/{token}")
def get_document_media(doc_id: UUID, token: str, db: Session = Depends(get_db)):
    bound_doc_id = validate_token(token)
    if not bound_doc_id or bound_doc_id != str(doc_id):
        raise HTTPException(status_code=404, detail="Not found")

    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Not found")

    doctor = db.query(Doctor).filter(Doctor.id == doc.doctor_id).first()
    doctor_user = db.query(User).filter(User.id == doctor.user_id).first() if doctor else None
    file_bytes, mime_type, ext = _render_doc_bytes(doc, doctor, doctor_user)

    filename = f"{doc.doc_type}_{str(doc.id)[:8]}.{ext}"
    return FastAPIResponse(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ── AUTHENTICATED DOWNLOAD - doctor downloading their own doc ─────────────────

@router.get("/{doc_id}/download")
def download_document(
    doc_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Authenticated download for the doctor. Same rendering as the public media
    endpoint, but requires JWT and returns attachment headers so the browser
    downloads instead of inlining.
    """
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doctor = _get_doctor(db, current_user)
    if doc.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="Not your document")

    doctor_user = db.query(User).filter(User.id == doctor.user_id).first()
    file_bytes, mime_type, ext = _render_doc_bytes(doc, doctor, doctor_user)

    label = _doc_label(doc).replace(" ", "_")
    filename = f"{label}_{str(doc.id)[:8]}.{ext}"
    return FastAPIResponse(
        content=file_bytes,
        media_type=mime_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET BY ID - kept last so other /{doc_id}/... routes match first ─

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
Write-Host "  Updated app/api/routes/documents.py - email channel, recall endpoint, authenticated download, X-Document-Id header on template generate" -ForegroundColor Green

git add .
git commit -m "Phase 3b - email send via Brevo, recall endpoint, authenticated download. POST /documents/{id}/send now accepts channel=email - renders bytes inline and attaches to a Brevo transactional email. New POST /documents/{id}/recall stamps recalled_at and sends a WhatsApp disregard message if the doc went via WhatsApp (email recall is doctor-manual deliberately). New GET /documents/{id}/download authenticated route returns the rendered file as attachment for the doctor to download. Template generate endpoint now returns X-Document-Id response header so frontend can wire Send buttons against template-generated docs. New app/services/email_brevo.py - lazy-loaded SDK, graceful failure when BREVO_API_KEY isnt configured."
Write-Host "Phase 3b backend committed locally. No migration needed - schema unchanged from 3a." -ForegroundColor Yellow