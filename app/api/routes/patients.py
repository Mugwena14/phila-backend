from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.db.database import get_db
from app.models.user import User
from app.models.patient_profile import PatientProfile
from app.schemas.patient_profile import PatientProfileUpdate, PatientProfileResponse
from app.core.security import decode_token

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