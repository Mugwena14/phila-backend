from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.schemas.user import UserCreate, UserResponse, Token
from app.core.security import hash_password, verify_password, create_access_token

from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import decode_token

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/register", response_model=UserResponse, status_code=201)
def register(data: UserCreate, db: Session = Depends(get_db)):
    # Check if email exists
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Check if phone exists as real account
    if db.query(User).filter(
        User.phone == data.phone,
        User.is_walk_in == False
    ).first():
        raise HTTPException(status_code=400, detail="Phone already registered")

    hashed = hash_password(data.password)
    user = User(
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        role=data.role,
        hashed_password=hashed,
        language_pref=data.language_pref,
        is_walk_in=False,
    )
    db.add(user)
    db.flush()

    # ── WALK-IN CLAIM FLOW ──────────────────────────────────────────
    # Check if a walk-in record exists with WALKIN_ prefix
    walkin_phone = f"WALKIN_{data.phone}"
    walkin_user = db.query(User).filter(
        User.phone == walkin_phone,
        User.claimed == False,
    ).first()

    if walkin_user:
        # Merge walk-in bookings to new account
        from app.models.booking import Booking
        db.query(Booking).filter(
            Booking.patient_id == walkin_user.id
        ).update({"patient_id": user.id})

        # Merge documents
        from app.models.patient_document import PatientDocument
        db.query(PatientDocument).filter(
            PatientDocument.patient_id == walkin_user.id
        ).update({"patient_id": user.id})

        # Mark walk-in as claimed
        walkin_user.claimed = True
        walkin_user.claimed_by = user.id

        logger.info(
            f"Walk-in record {walkin_user.id} claimed by new user {user.id}"
        )
    # ────────────────────────────────────────────────────────────────

    db.commit()
    db.refresh(user)
    return user

@router.post("/login", response_model=Token)
def login(email: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {"access_token": token, "token_type": "bearer"}



security_scheme = HTTPBearer()

def get_current_user_auth(
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
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

@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user_auth)):
    return current_user