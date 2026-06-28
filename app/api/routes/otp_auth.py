"""
OTP-verified registration routes. Mounted as a separate router so the
existing /auth/register and /auth/login stay untouched.

Flow:
  1. POST /auth/otp/request         {channel, identifier}        -> {sent: bool, error?: str}
  2. POST /auth/otp/verify          {channel, identifier, code}  -> {token: str, error?: str}
  3. POST /auth/register-verified   {full_name, channel, identifier, phone, password, registration_token}
       -> creates User, runs walk-in claim, returns access_token
"""
import logging
import re
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.user import User
from app.schemas.user import Token
from app.core.security import hash_password, create_access_token
from app.services.otp import (
    check_rate_limit,
    verify_code,
    issue_registration_token,
    verify_registration_token,
)
from app.services.otp_delivery import (
    send_otp_via_email,
    send_otp_via_whatsapp,
    send_otp_via_sms,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["otp_auth"])


# â”€â”€ Phone normalization (SA-specific for pilot) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def normalize_sa_phone(phone: str) -> Optional[str]:
    """
    Normalize SA phone numbers to +27XXXXXXXXX format.
    Accepts: 0685021117, +27685021117, 27685021117, 27 68 502 1117, etc.
    Returns None if the input doesn't look like a SA mobile number.
    """
    cleaned = re.sub(r"[^\d+]", "", phone)

    if cleaned.startswith("+27") and len(cleaned) == 12:
        return cleaned
    if cleaned.startswith("27") and len(cleaned) == 11:
        return "+" + cleaned
    if cleaned.startswith("0") and len(cleaned) == 10:
        return "+27" + cleaned[1:]
    return None


# â”€â”€ Request models â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
class OtpRequestPayload(BaseModel):
    channel: Literal["email", "whatsapp", "sms"]
    identifier: str  # email address OR phone number depending on channel


class OtpVerifyPayload(BaseModel):
    channel: Literal["email", "whatsapp", "sms"]
    identifier: str
    code: str


class RegisterVerifiedPayload(BaseModel):
    full_name: str
    email: EmailStr
    phone: str
    password: str
    channel: Literal["email", "whatsapp", "sms"]
    identifier: str           # the same identifier OTP-verified
    registration_token: str   # from POST /auth/otp/verify


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _resolve_identifier(channel: str, identifier: str) -> str:
    """Normalize phone numbers for phone-based channels. Email stays as-is
    (the OTP service lowercases it internally)."""
    if channel in ("whatsapp", "sms"):
        normalized = normalize_sa_phone(identifier)
        if not normalized:
            raise HTTPException(
                status_code=400,
                detail="Invalid phone number. Use SA format like 0685021117 or +27685021117.",
            )
        return normalized
    return identifier


# â”€â”€ Routes â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@router.post("/otp/request", status_code=200)
def request_otp(data: OtpRequestPayload):
    """Issue and send an OTP via the chosen channel."""
    ident = _resolve_identifier(data.channel, data.identifier)

    allowed, retry_after = check_rate_limit(data.channel, ident)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many OTP requests. Try again in {retry_after} seconds.",
        )

    if data.channel == "email":
        success, error = send_otp_via_email(ident)
    elif data.channel == "whatsapp":
        success, error = send_otp_via_whatsapp(ident)
    elif data.channel == "sms":
        success, error = send_otp_via_sms(ident)
    else:
        raise HTTPException(status_code=400, detail="Unknown channel")

    if not success:
        # Surface the error so the app can suggest a different channel
        raise HTTPException(status_code=502, detail=error or "Failed to send code")

    return {"sent": True, "channel": data.channel}


@router.post("/otp/verify", status_code=200)
def verify_otp(data: OtpVerifyPayload):
    """Verify an OTP code. On success returns a 5-minute registration token."""
    ident = _resolve_identifier(data.channel, data.identifier)

    success, message = verify_code(data.channel, ident, data.code)
    if not success:
        raise HTTPException(status_code=400, detail=message)

    token = issue_registration_token(data.channel, ident)
    return {"verified": True, "registration_token": token}


@router.post("/register-verified", response_model=Token, status_code=201)
def register_verified(data: RegisterVerifiedPayload, db: Session = Depends(get_db)):
    """
    Create a real user account after OTP verification.

    Reproduces the walk-in claim flow from /auth/register so manually-booked
    patients get their pending bookings/docs linked to the new account on
    signup.
    """
    # Normalize the OTP'd identifier
    verified_ident = _resolve_identifier(data.channel, data.identifier)

    # Validate the registration token matches the channel + identifier
    if not verify_registration_token(data.registration_token, data.channel, verified_ident):
        raise HTTPException(
            status_code=400,
            detail="Registration token invalid or expired. Please verify your code again.",
        )

    # Normalize the phone they want to register with (separate from the
    # OTP identifier - they could have OTPd via email but want a different
    # phone on file. Most cases these match but we dont enforce it.)
    normalized_phone = normalize_sa_phone(data.phone)
    if not normalized_phone:
        raise HTTPException(
            status_code=400,
            detail="Invalid phone number. Use SA format like 0685021117.",
        )

    # Email/phone uniqueness checks - same as /auth/register
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    if db.query(User).filter(
        User.phone == normalized_phone,
        User.is_walk_in == False,
    ).first():
        raise HTTPException(status_code=400, detail="Phone already registered")

    # Create the user
    user = User(
        full_name=data.full_name,
        email=data.email,
        phone=normalized_phone,
        role="patient",
        hashed_password=hash_password(data.password),
        language_pref="en",
        is_walk_in=False,
    )
    db.add(user)
    db.flush()

    # Walk-in claim flow - identical to /auth/register
    walkin_phone = f"WALKIN_{normalized_phone}"
    walkin_user = db.query(User).filter(
        User.phone == walkin_phone,
        User.claimed == False,
    ).first()

    if walkin_user:
        from app.models.booking import Booking
        db.query(Booking).filter(
            Booking.patient_id == walkin_user.id
        ).update({"patient_id": user.id})

        from app.models.patient_document import PatientDocument
        db.query(PatientDocument).filter(
            PatientDocument.patient_id == walkin_user.id
        ).update({"patient_id": user.id})

        walkin_user.claimed = True
        walkin_user.claimed_by = user.id
        logger.info(f"Walk-in {walkin_user.id} claimed by OTP-registered user {user.id}")

    db.commit()
    db.refresh(user)

    # Issue auth token same shape as login
    token = create_access_token({"sub": str(user.id), "role": user.role})
    return {"access_token": token, "token_type": "bearer"}