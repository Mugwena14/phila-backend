from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.db.database import get_db
from app.models.rating import Rating
from app.models.booking import Booking
from app.models.doctor import Doctor
from app.models.user import User
from app.schemas.rating import RatingCreate, RatingResponse
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/ratings", tags=["ratings"])
security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/", response_model=RatingResponse, status_code=201)
def submit_rating(
    data: RatingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter(Booking.id == data.booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if str(booking.patient_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="Not your booking")
    if booking.status not in ["completed", "no_show"]:
        raise HTTPException(status_code=400, detail="Can only rate completed appointments")

    existing = db.query(Rating).filter(Rating.booking_id == data.booking_id).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already rated this appointment")

    if not 1 <= data.rating <= 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")

    rating = Rating(
        patient_id=current_user.id,
        doctor_id=booking.doctor_id,
        booking_id=data.booking_id,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(rating)
    db.flush()

    # Recalculate doctor average rating
    doctor = db.query(Doctor).filter(Doctor.id == booking.doctor_id).first()
    if doctor:
        all_ratings = db.query(Rating).filter(Rating.doctor_id == booking.doctor_id).all()
        doctor.total_reviews = len(all_ratings)
        doctor.rating = round(sum(r.rating for r in all_ratings) / len(all_ratings), 1)

    db.commit()
    db.refresh(rating)
    return rating


@router.get("/booking/{booking_id}/can-rate")
def can_rate(
    booking_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    booking = db.query(Booking).filter(
        Booking.id == booking_id,
        Booking.patient_id == current_user.id,
    ).first()
    if not booking or booking.status not in ["completed", "no_show"]:
        return {"can_rate": False}
    existing = db.query(Rating).filter(Rating.booking_id == booking_id).first()
    return {"can_rate": existing is None}


@router.get("/doctor/{doctor_id}", response_model=List[RatingResponse])
def get_doctor_ratings(doctor_id: str, db: Session = Depends(get_db)):
    return (
        db.query(Rating)
        .filter(Rating.doctor_id == doctor_id)
        .order_by(Rating.created_at.desc())
        .limit(20)
        .all()
    )