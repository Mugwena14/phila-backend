from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.db.database import get_db
from app.models.user import User
from app.models.patient_profile import PatientProfile
from app.schemas.patient_profile import PatientProfileUpdate, PatientProfileResponse
from app.core.security import decode_token

from sqlalchemy import text
from uuid import UUID
from app.models.doctor import Doctor

router = APIRouter(prefix="/patients", tags=["patients"])
security = HTTPBearer()


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


@router.get("/profile", response_model=PatientProfileResponse)
def get_patient_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(PatientProfile).filter(
        PatientProfile.user_id == current_user.id
    ).first()

    if not profile:
        # Auto-create empty profile on first fetch
        profile = PatientProfile(user_id=current_user.id)
        db.add(profile)
        db.commit()
        db.refresh(profile)

    return profile


@router.put("/profile", response_model=PatientProfileResponse)
def update_patient_profile(
    data: PatientProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    profile = db.query(PatientProfile).filter(
        PatientProfile.user_id == current_user.id
    ).first()

    if not profile:
        profile = PatientProfile(user_id=current_user.id)
        db.add(profile)

    if data.height_cm is not None:
        profile.height_cm = data.height_cm
    if data.weight_kg is not None:
        profile.weight_kg = data.weight_kg
    if data.blood_type is not None:
        profile.blood_type = data.blood_type
    if data.date_of_birth is not None:
        profile.date_of_birth = data.date_of_birth

    db.commit()
    db.refresh(profile)
    return profile


@router.get("/")
def get_all_patients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all patients who have booked with this doctor."""
    from app.models.booking import Booking

    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    # Get all unique patient IDs from bookings
    patient_ids = db.execute(
        text("SELECT DISTINCT patient_id FROM bookings WHERE doctor_id = :doc_id"),
        {"doc_id": str(doctor.id)}
    ).fetchall()

    patients = []
    for row in patient_ids:
        patient = db.query(User).filter(User.id == row.patient_id).first()
        if patient:
            patients.append({
                "id": str(patient.id),
                "full_name": patient.full_name,
                "phone": patient.phone,
                "is_walk_in": getattr(patient, 'is_walk_in', False),
                "claim_code": getattr(patient, 'claim_code', None),
                "claimed": getattr(patient, 'claimed', False),
                "created_at": str(patient.created_at) if patient.created_at else None,
            })

    return patients


@router.get("/{patient_id}/medications")
def get_patient_medications(
    patient_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get all tracked medications for a patient."""
    from app.models.patient_medication import PatientMedication

    meds = db.query(PatientMedication).filter(
        PatientMedication.patient_id == patient_id
    ).all()

    return [
        {
            "id": str(m.id),
            "medication_name": m.medication_name,
            "last_prescribed_date": m.last_prescribed_date,
            "estimated_refill_date": m.estimated_refill_date,
            "refill_notified": m.refill_notified,
        }
        for m in meds
    ]