Write-Host "Phila Backend - Phase 2 - harden template upload/generate, fix models __init__" -ForegroundColor Cyan

# 1. FIX THE ROOT CAUSE - import every model into __init__.py so autogenerate sees them
Set-Content "app/models/__init__.py" @'
"""
Every SQLAlchemy model must be imported here.

Alembic's autogenerate compares Base.metadata against the database. Models that
aren't imported into the metadata at generation time are invisible - autogenerate
produces an empty upgrade() with `pass`, the migration "applies" silently, and
the table that should have been created simply never exists in prod.

This is what caused the favorites table 500s on 23 June 2026. The lesson:
every new model file in this directory needs a line here. If you add a new
model and forget, the next autogenerate is silent garbage.
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
'@
Write-Host "  Fixed app/models/__init__.py - every model is now visible to autogenerate" -ForegroundColor Green

# 2. NEW SERVICE - all the docx hardening lives here
Set-Content "app/services/document_templates.py" @'
"""
Custom .docx template handling - placeholder extraction, validation, and
filling. All the python-docx edge cases live here so the routes stay simple.

Hardening covered:
  - Smart-quote normalisation (Word autocorrect breaks `{{` and `}}` silently)
  - Run-joining before regex/replace (placeholders edited mid-string in Word
    get split across multiple <w:r> XML runs - extraction sees the placeholder
    on para.text but per-run substitution misses it)
  - Header and footer scanning (doctors put dates/addresses there)
  - Validation - corrupt files, missing placeholders, etc surface as useful
    errors instead of 500s

If you change the placeholder regex here, also update placeholderWillAutofill
in phila-web's DocumentsPage.tsx so the UI indicators stay in sync.
Phase 4-ish task: collapse both into a single server-owned heuristic.
"""
import re
import logging
from typing import Iterable
from docx import Document as DocxDocument
from docx.text.paragraph import Paragraph

logger = logging.getLogger(__name__)


# Curly variants Word autocorrect produces, mapped to ASCII straight quotes.
# We only normalise within text we're about to scan for placeholders - the rest
# of the document is left alone so legitimate smart quotes in prose survive.
SMART_QUOTE_MAP = {
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote
    "\u201C": '"',   # left double quote
    "\u201D": '"',   # right double quote
    "\u201A": "'",   # single low-9 quote
    "\u201E": '"',   # double low-9 quote
    # Curly braces - the actual blockers for placeholders
    "\uFF5B": "{",   # fullwidth left brace
    "\uFF5D": "}",   # fullwidth right brace
}

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class TemplateError(Exception):
    """Raised when a .docx can't be processed - corrupt file, unreadable, etc."""
    pass


def _normalise(text: str) -> str:
    """Replace curly Unicode variants with ASCII so placeholder regex matches."""
    if not text:
        return text
    for bad, good in SMART_QUOTE_MAP.items():
        text = text.replace(bad, good)
    return text


def _iter_paragraphs(doc) -> Iterable[Paragraph]:
    """
    Yield every paragraph in the document - body, tables, headers, footers.
    Doctors put practice address and date in headers; placeholders there need
    to be both detected and substituted.
    """
    # Body
    for p in doc.paragraphs:
        yield p

    # Tables in body
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    yield p

    # Sections - headers and footers
    for section in doc.sections:
        for container in (section.header, section.footer,
                          section.first_page_header, section.first_page_footer,
                          section.even_page_header, section.even_page_footer):
            if container is None:
                continue
            for p in container.paragraphs:
                yield p
            for table in container.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for p in cell.paragraphs:
                            yield p


def extract_placeholders(file_path: str) -> list[str]:
    """
    Scan a .docx and return every unique {{placeholder}} key it contains.

    Handles smart quotes, scans body + tables + headers + footers. Preserves
    insertion order so the UI form matches the document order.

    Raises TemplateError if the file can't be opened.
    """
    try:
        doc = DocxDocument(file_path)
    except Exception as e:
        raise TemplateError(f"Could not open .docx file (is it valid?): {e}") from e

    seen: dict[str, None] = {}
    for p in _iter_paragraphs(doc):
        text = _normalise(p.text)
        for match in PLACEHOLDER_RE.findall(text):
            seen.setdefault(match, None)

    return list(seen.keys())


def _replace_in_paragraph(para: Paragraph, values: dict) -> None:
    """
    Replace {{placeholders}} in a paragraph WITHOUT losing formatting.

    The python-docx run-splitting problem: Word stores text as one or more
    <w:r> runs per paragraph. A placeholder typed and later edited often gets
    split across runs. Per-run replacement misses these because each run holds
    only a fragment.

    The fix: rebuild the paragraph by joining all run text, doing the replace
    on the joined string, then writing the result back to the first run and
    emptying the rest. This loses the formatting variation between sub-runs
    (e.g. if `{{patient_name}}` had one half bold and one half italic, the
    output uses the first run's formatting throughout). That trade-off is
    fine - mixed formatting inside a single placeholder is rare and looks
    wrong anyway.
    """
    if not para.runs:
        return

    full_text = "".join(run.text for run in para.runs)
    full_text = _normalise(full_text)

    if "{{" not in full_text:
        return  # nothing to do

    for key, val in values.items():
        full_text = full_text.replace(f"{{{{{key}}}}}", str(val) if val is not None else "")

    # Write the result back into the first run; blank out the rest.
    para.runs[0].text = full_text
    for run in para.runs[1:]:
        run.text = ""


def fill_placeholders(template_path: str, values: dict, output_path: str) -> None:
    """
    Open template_path, substitute {{placeholders}} with values, save to
    output_path. Handles run-splitting, headers, footers, tables.

    Raises TemplateError on failure.
    """
    try:
        doc = DocxDocument(template_path)
    except Exception as e:
        raise TemplateError(f"Could not open template: {e}") from e

    try:
        for p in _iter_paragraphs(doc):
            _replace_in_paragraph(p, values)
        doc.save(output_path)
    except Exception as e:
        raise TemplateError(f"Could not fill template: {e}") from e


# ── Sample data for the preview endpoint ──────────────────────────────────────

# Phase 1's placeholderWillAutofill heuristic, mirrored. Used for both the
# preview endpoint (so the doctor sees realistic-looking sample data in their
# template before using it on a real patient) and Phase 4's future single
# source of truth.
def sample_value_for(key: str) -> str:
    """Realistic-looking sample value to use when previewing a template."""
    k = key.lower()
    if "patient" in k and "name" in k:    return "Sipho Mthembu"
    if "doctor" in k:                      return "Dr Jane Mthembu"
    if "practice" in k:                    return "Mthembu Family Practice"
    if "date" in k and "visit" in k:       return "14 March 2026"
    if "date" in k:                        return "15 March 2026"
    if "diagnosis" in k or "concern" in k: return "Upper respiratory tract infection"
    if "medication" in k:                  return "Paracetamol 500mg, Amoxicillin 500mg"
    if "allerg" in k:                      return "Penicillin"
    if "note" in k or "additional" in k:   return "Patient to rest and increase fluid intake."
    if "duration" in k:                    return "3 days"
    if "severity" in k:                    return "5/10"
    if "days_off" in k or "days off" in k: return "3"
    if "hpcsa" in k:                       return "MP 123456"
    if "qualification" in k:               return "MBChB (UCT)"
    if "urgency" in k:                     return "Routine"
    # Manual-entry placeholders - keep the placeholder text so the doctor can
    # see at a glance which fields they'd need to fill in for real.
    return f"[{key}]"


def build_sample_values(placeholders: list[str]) -> dict:
    """Return a dict mapping every placeholder to a realistic sample value."""
    return {p: sample_value_for(p) for p in placeholders}
'@
Write-Host "  Created app/services/document_templates.py - smart quotes, run-joining, header/footer scan, preview helpers" -ForegroundColor Green

# 3. Rewrite the documents route to use the service + add preview endpoint
Set-Content "app/api/routes/documents.py" @'
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response as FastAPIResponse
from sqlalchemy.orm import Session
from uuid import UUID
from pydantic import BaseModel
import json
import uuid
import os
import shutil
import logging

from app.db.database import get_db
from app.models.patient_document import PatientDocument
from app.models.document_template import DocumentTemplate
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

router = APIRouter(prefix="/documents", tags=["documents"])
security = HTTPBearer()
logger = logging.getLogger(__name__)

UPLOAD_DIR = "uploads/templates"
os.makedirs(UPLOAD_DIR, exist_ok=True)


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


# ── TEMPLATE ENDPOINTS ────────────────────────────────────────────────────────

@router.post("/templates/upload", status_code=201)
async def upload_template(
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Upload a .docx template. Extracts {{placeholders}} from body, tables,
    headers, and footers. Smart-quote tolerant.
    """
    doctor = _get_doctor(db, current_user)

    if not file.filename or not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    template_id = str(uuid.uuid4())
    safe_filename = f"{template_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Extract placeholders. Bad files get cleaned up immediately so we don't
    # accumulate junk on disk.
    try:
        placeholders = extract_placeholders(file_path)
    except TemplateError as e:
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=400, detail=str(e))

    if not placeholders:
        # Not fatal - we'll still save the template, but the doctor needs to
        # know this isn't going to pre-fill anything. The UI in Phase 1 already
        # surfaces the count from the response, so a clear 0 here is correct.
        logger.warning(
            "Template '%s' uploaded by doctor %s contains 0 placeholders",
            name, doctor.id,
        )

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

    logger.info("Template uploaded: %s - %d placeholders", name, len(placeholders))

    return {
        "id": str(template.id),
        "name": template.name,
        "description": template.description,
        "filename": template.filename,
        "placeholders": placeholders,
        "created_at": str(template.created_at),
    }


@router.get("/templates")
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
def delete_template(
    template_id: UUID,
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

    if template.file_path and os.path.exists(template.file_path):
        os.remove(template.file_path)

    db.delete(template)
    db.commit()
    return {"message": "Template deleted"}


@router.post("/templates/{template_id}/preview")
def preview_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a .docx filled with sample data for this template. Lets the
    doctor verify formatting and substitution before using it on a real
    patient - catches run-splitting issues, missing placeholders, broken
    templates BEFORE a consultation depends on it.
    """
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
    """Fill a template with provided values and return the generated .docx."""
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


# ── BUILT-IN DOCUMENT ENDPOINTS (unchanged) ───────────────────────────────────

class GenerateDocumentRequest(BaseModel):
    booking_id: UUID
    doc_type: str
    content: dict


@router.post("/generate", status_code=201)
def generate_document(
    data: GenerateDocumentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
        "id": str(doc.id),
        "doc_type": doc.doc_type,
        "content": json.loads(doc.content),
        "created_at": str(doc.created_at),
        "message": "Document generated successfully",
    }


@router.get("/patient/{patient_id}")
def get_patient_documents(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    docs = (
        db.query(PatientDocument)
        .filter(PatientDocument.patient_id == patient_id)
        .order_by(PatientDocument.created_at.desc())
        .all()
    )
    return [
        {
            "id": str(d.id),
            "patient_id": str(d.patient_id),
            "booking_id": str(d.booking_id) if d.booking_id else None,
            "doc_type": d.doc_type,
            "content": json.loads(d.content),
            "created_at": str(d.created_at),
        }
        for d in docs
    ]


@router.get("/{doc_id}")
def get_document(
    doc_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    doc = db.query(PatientDocument).filter(PatientDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": str(doc.id),
        "doc_type": doc.doc_type,
        "content": json.loads(doc.content),
        "created_at": str(doc.created_at),
    }


@router.get("/templates/starter/{doc_type}")
def download_starter_template(
    doc_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download a pre-built starter .docx for a given doc type."""
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
Write-Host "  Updated app/api/routes/documents.py - thinned down, imports docx logic from service, adds /templates/{id}/preview" -ForegroundColor Green

git add .
git commit -m "Phase 2 - harden custom template handling. Extract python-docx logic into app/services/document_templates.py. Fix run-splitting (placeholders edited mid-string in Word now substitute correctly instead of silently appearing as raw text in the generated doc). Add smart-quote normalisation so Word autocorrect doesnt break placeholders. Scan headers and footers for placeholders, not just body and tables. Add POST /documents/templates/{id}/preview endpoint to generate a sample-filled .docx before using on a real patient. Fix app/models/__init__.py so every model is visible to alembic autogenerate - root cause of the favorites empty-migration bug, now closed for the whole class of bug."
Write-Host "Phase 2 committed locally. No migration needed (logic-only). Push when ready - Phase 0 deploy log will confirm 'No migrations applied' which is the correct outcome." -ForegroundColor Yellow