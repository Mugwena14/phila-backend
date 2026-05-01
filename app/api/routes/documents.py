from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID
from typing import List, Optional
from pydantic import BaseModel
import json
import uuid

from app.db.database import get_db
from app.models.patient_document import PatientDocument
from app.models.user import User
from app.models.doctor import Doctor
from app.models.booking import Booking
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging

router = APIRouter(prefix="/documents", tags=["documents"])
security = HTTPBearer()
logger = logging.getLogger(__name__)


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


class GenerateDocumentRequest(BaseModel):
    booking_id: UUID
    doc_type: str  # sick_letter | medical_certificate | referral_letter | visit_summary
    content: dict  # Doctor fills this in — template fields


class DocumentResponse(BaseModel):
    id: UUID
    patient_id: UUID
    booking_id: Optional[UUID]
    doc_type: str
    content: dict
    created_at: str

    class Config:
        from_attributes = True


@router.post("/generate", status_code=201)
def generate_document(
    data: GenerateDocumentRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Generate a document for a patient visit."""
    doctor = db.query(Doctor).filter(
        Doctor.user_id == current_user.id
    ).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    booking = db.query(Booking).filter(
        Booking.id == data.booking_id
    ).first()
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
    """Get all documents for a patient."""
    docs = db.query(PatientDocument).filter(
        PatientDocument.patient_id == patient_id
    ).order_by(PatientDocument.created_at.desc()).all()

    return [
        {
            "id": str(d.id),
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
    """Get a single document."""
    doc = db.query(PatientDocument).filter(
        PatientDocument.id == doc_id
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": str(doc.id),
        "doc_type": doc.doc_type,
        "content": json.loads(doc.content),
        "created_at": str(doc.created_at),
    }