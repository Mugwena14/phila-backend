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
