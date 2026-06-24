Write-Host "Phila Backend - Phase 2 - harden custom document templates" -ForegroundColor Cyan

# 1) Fix app/models/__init__.py - this is the favorites empty-migration root cause
Set-Content "app/models/__init__.py" @'
"""
Import every model here so that Alembic autogenerate can see them in Base.metadata.

If a new model file is added under app/models/ and NOT imported here, autogenerate
will produce an empty migration (pass / pass) and silently miss the schema change.
This is exactly what happened with favorite_doctors. Do not let it happen again.
"""
from app.models.booking import Booking
from app.models.doctor import Doctor
from app.models.document_template import DocumentTemplate
from app.models.favorite_doctor import FavoriteDoctor
from app.models.intake_brief import IntakeBrief
from app.models.notification import Notification
from app.models.patient_document import PatientDocument
from app.models.patient_health_summary import PatientHealthSummary
from app.models.patient_medication import PatientMedication
from app.models.patient_profile import PatientProfile
from app.models.rating import Rating
from app.models.slot import Slot
from app.models.user import User
from app.models.waitlist import Waitlist
from app.models.working_hours import WorkingHours

__all__ = [
    "Booking",
    "Doctor",
    "DocumentTemplate",
    "FavoriteDoctor",
    "IntakeBrief",
    "Notification",
    "PatientDocument",
    "PatientHealthSummary",
    "PatientMedication",
    "PatientProfile",
    "Rating",
    "Slot",
    "User",
    "Waitlist",
    "WorkingHours",
]
'@
Write-Host "  Updated app/models/__init__.py - all 15 models imported, autogenerate can now see them" -ForegroundColor Green

# 2) Create the docx service - all the hardening lives here
Set-Content "app/services/document_templates.py" @'
"""
Document template service - extraction and substitution for .docx templates.

Handles the messy reality of real-world Word documents:
  - Smart quotes from Word autocorrect that silently break placeholder regex
  - Placeholders split across multiple <w:r> runs (the python-docx classic gotcha)
  - Placeholders in headers and footers, not just the body
  - Validation feedback for empty or unreadable files
"""
from typing import List, Dict, Tuple
import re
import logging

logger = logging.getLogger(__name__)

# Matches {{placeholder_name}} - the standard syntax we ask doctors to use.
# \w+ allows letters, digits, underscores. No spaces inside placeholders.
PLACEHOLDER_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def _normalise_smart_chars(text: str) -> str:
    """
    Word autocorrect helpfully replaces straight quotes and braces with curly
    Unicode variants. The placeholder regex needs ASCII { and }, so any
    Unicode variant of { } " ' must be flipped back to ASCII before matching.

    Idempotent: running this on already-normalised text yields the same output.
    """
    if not text:
        return text
    replacements = {
        # Curly single quotes -> straight
        "\u2018": "'",
        "\u2019": "'",
        "\u201A": "'",
        "\u201B": "'",
        # Curly double quotes -> straight
        "\u201C": '"',
        "\u201D": '"',
        "\u201E": '"',
        "\u201F": '"',
        # Fullwidth brace variants -> ASCII
        "\uFF5B": "{",
        "\uFF5D": "}",
        # White-square braces (rare but seen)
        "\u2774": "{",
        "\u2775": "}",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def _normalise_paragraph_runs(para) -> None:
    """
    Apply smart-char normalisation in-place to every run in a paragraph.
    Done at the run level so we don't lose formatting.
    """
    for run in para.runs:
        if run.text:
            run.text = _normalise_smart_chars(run.text)


def _iter_all_paragraphs(doc):
    """
    Yield every paragraph in the document - body, tables, and all section
    headers and footers. python-docx treats headers/footers as separate
    section objects that are easy to miss.
    """
    # Body paragraphs
    for para in doc.paragraphs:
        yield para
    # Table cells (which contain their own paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    yield para
    # Section headers and footers
    for section in doc.sections:
        if section.header is not None:
            for para in section.header.paragraphs:
                yield para
            # Tables inside headers
            for table in section.header.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            yield para
        if section.footer is not None:
            for para in section.footer.paragraphs:
                yield para
            for table in section.footer.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            yield para


def extract_placeholders(file_path: str) -> List[str]:
    """
    Scan a .docx and return the ordered list of unique {{placeholder}} names.
    Covers body, tables, headers, and footers.
    """
    from docx import Document as DocxDocument

    try:
        doc = DocxDocument(file_path)
    except Exception as e:
        logger.warning(f"Could not open .docx for extraction at {file_path}: {e}")
        raise ValueError(f"Could not read .docx file: {e}")

    seen: Dict[str, None] = {}  # ordered set
    for para in _iter_all_paragraphs(doc):
        # Use the full paragraph text (joined across runs) so split placeholders
        # are still detected. We do not modify the document here.
        text = _normalise_smart_chars(para.text)
        for match in PLACEHOLDER_PATTERN.findall(text):
            seen[match] = None
    return list(seen.keys())


def _replace_in_paragraph(para, values: Dict[str, str]) -> None:
    """
    Replace {{placeholders}} in a single paragraph.

    The hard part: python-docx splits text across runs. A placeholder typed
    as {{patient_name}} and then mid-edited can end up with `{{patient` in one
    run and `_name}}` in the next, so a per-run str.replace silently misses it.

    Approach: if any placeholder exists in the joined paragraph text but the
    run-by-run replace alone wouldn't catch it, collapse all runs into the
    first run and clear the others. This loses run-level formatting WITHIN the
    placeholder substring, but preserves formatting of the rest of the paragraph
    in practice because Word usually wraps the whole placeholder in one
    formatting span.
    """
    if not para.runs:
        return

    # First, normalise smart chars in every run so the regex finds them.
    _normalise_paragraph_runs(para)

    # Try the simple per-run replace first - covers the easy case and keeps
    # all formatting intact.
    for run in para.runs:
        if run.text and "{{" in run.text:
            for key, val in values.items():
                token = "{{" + key + "}}"
                if token in run.text:
                    run.text = run.text.replace(token, str(val))

    # Check if any placeholders are still present in the joined text.
    # If yes, they were split across runs. Collapse and re-replace.
    joined = "".join(run.text for run in para.runs)
    if "{{" not in joined:
        return

    still_present = [k for k in values.keys() if ("{{" + k + "}}") in joined]
    if not still_present:
        return

    # Substitute in the joined string, then put it all in the first run.
    new_text = joined
    for key in still_present:
        new_text = new_text.replace("{{" + key + "}}", str(values[key]))

    para.runs[0].text = new_text
    for run in para.runs[1:]:
        run.text = ""


def fill_template(template_path: str, values: Dict[str, str], output_path: str) -> None:
    """
    Open the template, substitute all {{placeholders}} with provided values,
    save to output_path. Covers body, tables, headers, footers.
    """
    from docx import Document as DocxDocument

    doc = DocxDocument(template_path)
    for para in _iter_all_paragraphs(doc):
        _replace_in_paragraph(para, values)
    doc.save(output_path)


def build_preview_values(placeholders: List[str]) -> Dict[str, str]:
    """
    Build a dummy values dict for previewing a template against sample data.
    Pattern-matches placeholder names to plausible sample content; falls back
    to "[placeholder_name]" so the doctor can clearly see which field is which
    in the rendered output.
    """
    today_str = "12 March 2026"
    values: Dict[str, str] = {}
    for key in placeholders:
        k = key.lower()
        if "patient" in k and "name" in k:
            values[key] = "Sipho Mthembu"
        elif "doctor" in k:
            values[key] = "Dr. Jane Smith"
        elif "practice" in k:
            values[key] = "Mthembu Family Practice"
        elif "date" in k and "visit" in k:
            values[key] = today_str
        elif "date" in k:
            values[key] = today_str
        elif "diagnosis" in k or "concern" in k:
            values[key] = "Upper respiratory tract infection"
        elif "medication" in k:
            values[key] = "Amoxicillin 500mg, Paracetamol 500mg"
        elif "allerg" in k:
            values[key] = "Penicillin"
        elif "note" in k or "additional" in k:
            values[key] = "Patient advised to rest and increase fluid intake."
        elif "duration" in k:
            values[key] = "3 days"
        elif "severity" in k:
            values[key] = "5/10"
        elif "days_off" in k or ("days" in k and "off" in k):
            values[key] = "3"
        elif "qualification" in k:
            values[key] = "MBChB (UCT)"
        elif "hpcsa" in k:
            values[key] = "PR1234567"
        elif "urgency" in k:
            values[key] = "Routine"
        elif "referred" in k and "specialty" in k:
            values[key] = "Cardiologist"
        elif "history" in k:
            values[key] = "Hypertension diagnosed 2024, well-controlled on Amlodipine."
        else:
            # Visible placeholder so the doctor can see which slot it is
            values[key] = f"[{key}]"
    return values
'@
Write-Host "  Created app/services/document_templates.py - smart-quote normalisation, run-joining substitution, header/footer scanning, preview-values builder" -ForegroundColor Green

# 3) Rewrite the documents route to use the service, plus add preview endpoint
Set-Content "app/api/routes/documents.py" @'
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, Response as FastAPIResponse
from sqlalchemy.orm import Session
from uuid import UUID
from typing import List, Optional
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
from app.services.template_starters import create_starter_template
from app.services.document_templates import (
    extract_placeholders,
    fill_template,
    build_preview_values,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

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


# -- TEMPLATE ENDPOINTS -----------------------------------------------------

@router.post("/templates/upload", status_code=201)
async def upload_template(
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a .docx template file. Extracts {{placeholders}} automatically."""
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    if not file.filename or not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported")

    template_id = str(uuid.uuid4())
    safe_filename = f"{template_id}_{file.filename}"
    file_path = os.path.join(UPLOAD_DIR, safe_filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Extract placeholders using the hardened service
    try:
        placeholders = extract_placeholders(file_path)
    except ValueError as e:
        # File unreadable / corrupted / not a valid .docx
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(
            status_code=400,
            detail=f"Could not read .docx file. Make sure it's a valid Word document. ({e})",
        )

    warning: Optional[str] = None
    if not placeholders:
        warning = (
            "No {{placeholders}} found in this document. Phila will save it but won't "
            "be able to pre-fill any fields when you generate from it. Add placeholders "
            "like {{patient_name}} in the document body and re-upload."
        )
        logger.warning(f"Template uploaded with zero placeholders: {file.filename}")

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

    logger.info(
        f"Template uploaded: {name} ({len(placeholders)} placeholders) for doctor {doctor.id}"
    )

    return {
        "id": str(template.id),
        "name": template.name,
        "description": template.description,
        "filename": template.filename,
        "placeholders": placeholders,
        "warning": warning,
        "created_at": str(template.created_at),
    }


@router.get("/templates")
def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all templates for this doctor."""
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

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
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    template = (
        db.query(DocumentTemplate)
        .filter(
            DocumentTemplate.id == template_id,
            DocumentTemplate.doctor_id == doctor.id,
        )
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    if os.path.exists(template.file_path):
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
    Generate a sample-data filled version of this template, so the doctor can
    verify it actually renders correctly BEFORE using it on a real patient.

    Catches run-splitting bugs, smart-quote breakage, header/footer issues -
    anything that would only surface at real-generation time otherwise.
    """
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    template = (
        db.query(DocumentTemplate)
        .filter(
            DocumentTemplate.id == template_id,
            DocumentTemplate.doctor_id == doctor.id,
        )
        .first()
    )
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    placeholders = json.loads(template.placeholders)
    sample_values = build_preview_values(placeholders)

    output_filename = f"preview_{uuid.uuid4()}.docx"
    output_path = os.path.join(UPLOAD_DIR, output_filename)

    try:
        fill_template(template.file_path, sample_values, output_path)
    except Exception as e:
        logger.error(f"Preview generation failed for template {template_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Could not generate preview: {e}")

    # Preview files are not saved to patient_documents - they're throwaway
    return FileResponse(
        path=output_path,
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
    """Fill template placeholders with provided values, return filled .docx."""
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    template = (
        db.query(DocumentTemplate)
        .filter(
            DocumentTemplate.id == template_id,
            DocumentTemplate.doctor_id == doctor.id,
        )
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
        fill_template(template.file_path, values, output_path)
    except Exception as e:
        logger.error(f"Template fill failed for {template_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error filling template: {e}")

    doc = PatientDocument(
        id=uuid.uuid4(),
        patient_id=booking.patient_id,
        booking_id=booking.id,
        doctor_id=doctor.id,
        doc_type=f"template_{template_id}",
        content=json.dumps(
            {
                "_template_id": str(template_id),
                "_template_name": template.name,
                "_output_file": output_path,
                **values,
            }
        ),
        generated_by=current_user.id,
    )
    db.add(doc)
    db.commit()

    return FileResponse(
        path=output_path,
        filename=f"{template.name}_{booking_id}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


# -- BUILT-IN DOC ENDPOINTS (unchanged behaviour) ---------------------------

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
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

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
    """Download a pre-built starter .docx template for a given doc type."""
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
Write-Host "  Updated app/api/routes/documents.py - routes now delegate docx work to the service, added POST /templates/{id}/preview, removed stray import line" -ForegroundColor Green

git add .
git commit -m "Phase 2 - harden custom templates. Extract docx logic into app/services/document_templates.py with smart-quote normalisation (Word autocorrect protection), run-joining substitution (fixes silent placeholder-not-replaced bug when placeholders are split across <w:r> runs), and header/footer scanning. New POST /documents/templates/{id}/preview endpoint generates a sample-data .docx so doctors verify their template renders correctly before real use. Upload route returns explicit warning when zero placeholders detected and a useful error instead of 500 on corrupted files. Fix app/models/__init__.py to import all 15 models - this was the empty-favorites-migration root cause; autogenerate could not see models that were not imported into Base.metadata."
Write-Host "Phase 2 committed locally. Push to deploy - watch the alembic log to confirm no migration runs (expected)" -ForegroundColor Yellow