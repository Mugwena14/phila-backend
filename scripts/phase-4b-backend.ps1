Write-Host "Phila Backend - Phase 4b - OTP-verified registration" -ForegroundColor Cyan

# ── 1. New service - OTP issue/verify with Redis storage + rate limiting ──────
[System.IO.File]::WriteAllText("$PWD\app\services\otp.py", @'
"""
One-time-password service backed by Redis.

Three channels supported:
  - email    (live, via Brevo)
  - whatsapp (built, blocked by Twilio sandbox until business sender approved)
  - sms      (built, requires TWILIO_SMS_FROM env var - intentionally unset)

Design notes:
  - Identifier is the destination address (email or normalized phone).
  - Codes are 6 digits, 5-minute TTL, 3 verify attempts before invalidation.
  - Rate limit: 3 OTP requests per identifier per 15 minutes.
  - On verify success, a short-lived (5-min) JWT-style registration token
    is returned. The token contains the verified identifier and channel,
    and is required at the new POST /auth/register-verified endpoint.
  - We never store the code in Postgres - Redis only. POPIA-friendly:
    once verified or expired, the code disappears.

Why a separate registration token rather than just trusting subsequent
calls: we want a stateless way for the patient app to call register-verified
without re-proving OTP. The token is signed with SECRET_KEY so the app
cant forge one, has a 5-min TTL so a leaked token expires fast, and the
register-verified endpoint validates the token before creating the user.
"""
import os
import random
import string
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import redis
import jwt

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")

# Constants - tuned for SA-pilot scale, revisit at 1000+ doctors
CODE_TTL_SECONDS = 300                  # 5 minutes
MAX_VERIFY_ATTEMPTS = 3
RATE_LIMIT_WINDOW_SECONDS = 900         # 15 minutes
MAX_REQUESTS_PER_WINDOW = 3
REGISTRATION_TOKEN_TTL_SECONDS = 300    # matches code TTL - cant re-use after code expiry

# Redis key namespaces
CODE_KEY_PREFIX = "otp:code:"
ATTEMPTS_KEY_PREFIX = "otp:attempts:"
RATE_LIMIT_KEY_PREFIX = "otp:ratelimit:"


def _redis() -> redis.Redis:
    """Lazy connection. Per-request connection is fine at this scale."""
    return redis.from_url(REDIS_URL, decode_responses=True)


def _generate_code() -> str:
    """6-digit numeric, leading zeros allowed."""
    return "".join(random.choices(string.digits, k=6))


def _normalize_identifier(channel: str, identifier: str) -> str:
    """
    Lowercase emails so OTP:foo@x.com matches OTP:FOO@x.com.
    Phone numbers should already be normalized by the route, but defensive
    here.
    """
    if channel == "email":
        return identifier.strip().lower()
    return identifier.strip()


def check_rate_limit(channel: str, identifier: str) -> tuple[bool, Optional[int]]:
    """
    Returns (allowed, retry_after_seconds).
    Uses Redis INCR + EXPIRE for a sliding-ish window. Not perfect but
    cheap and good enough for SMS-bomb mitigation.
    """
    r = _redis()
    ident = _normalize_identifier(channel, identifier)
    key = f"{RATE_LIMIT_KEY_PREFIX}{channel}:{ident}"

    count = r.incr(key)
    if count == 1:
        # First request in this window - set TTL
        r.expire(key, RATE_LIMIT_WINDOW_SECONDS)

    if count > MAX_REQUESTS_PER_WINDOW:
        ttl = r.ttl(key)
        return False, ttl if ttl > 0 else RATE_LIMIT_WINDOW_SECONDS
    return True, None


def issue_code(channel: str, identifier: str) -> str:
    """
    Generate, store, and return a fresh OTP code. Overwrites any existing
    code for the same identifier (one active code at a time per channel:identifier).
    Resets the attempt counter.

    Does NOT send the code - the caller does that (different channels use
    different services). Returns the plain code so caller can deliver it.
    """
    r = _redis()
    ident = _normalize_identifier(channel, identifier)
    code = _generate_code()

    code_key = f"{CODE_KEY_PREFIX}{channel}:{ident}"
    attempts_key = f"{ATTEMPTS_KEY_PREFIX}{channel}:{ident}"

    # Set both keys atomically-ish (pipeline doesnt give strict atomicity
    # but is close enough for our case)
    pipe = r.pipeline()
    pipe.setex(code_key, CODE_TTL_SECONDS, code)
    pipe.delete(attempts_key)  # fresh attempt counter for the new code
    pipe.execute()

    logger.info(f"OTP issued for {channel}:{ident} (TTL {CODE_TTL_SECONDS}s)")
    return code


def verify_code(channel: str, identifier: str, code: str) -> tuple[bool, str]:
    """
    Returns (success, message).
    On success, the code is consumed (deleted). On failure, attempt counter
    increments and code is invalidated after MAX_VERIFY_ATTEMPTS wrong guesses.
    """
    r = _redis()
    ident = _normalize_identifier(channel, identifier)

    code_key = f"{CODE_KEY_PREFIX}{channel}:{ident}"
    attempts_key = f"{ATTEMPTS_KEY_PREFIX}{channel}:{ident}"

    stored_code = r.get(code_key)
    if not stored_code:
        return False, "Code expired or never issued. Request a new one."

    if code.strip() == stored_code:
        # Consume on success
        r.delete(code_key)
        r.delete(attempts_key)
        logger.info(f"OTP verified successfully for {channel}:{ident}")
        return True, "Verified"

    # Wrong code - increment attempts
    attempts = r.incr(attempts_key)
    r.expire(attempts_key, CODE_TTL_SECONDS)  # match code TTL

    if attempts >= MAX_VERIFY_ATTEMPTS:
        r.delete(code_key)
        r.delete(attempts_key)
        logger.warning(f"OTP invalidated after {attempts} failed attempts for {channel}:{ident}")
        return False, "Too many wrong attempts. Request a new code."

    remaining = MAX_VERIFY_ATTEMPTS - attempts
    return False, f"Wrong code. {remaining} attempt(s) remaining."


def issue_registration_token(channel: str, identifier: str) -> str:
    """
    Short-lived JWT proving the holder successfully verified an OTP on this
    channel:identifier. Used at POST /auth/register-verified to skip
    re-verifying.
    """
    ident = _normalize_identifier(channel, identifier)
    payload = {
        "channel": channel,
        "identifier": ident,
        "purpose": "registration",
        "exp": datetime.now(timezone.utc) + timedelta(seconds=REGISTRATION_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def verify_registration_token(token: str, expected_channel: str, expected_identifier: str) -> bool:
    """
    Returns True if the token is valid, unexpired, and matches the channel
    and identifier the user is now trying to register with. Stops a user
    from verifying email A and then signing up with email B.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False

    if payload.get("purpose") != "registration":
        return False
    if payload.get("channel") != expected_channel:
        return False
    if payload.get("identifier") != _normalize_identifier(expected_channel, expected_identifier):
        return False
    return True
'@)
Write-Host "  Created app/services/otp.py" -ForegroundColor Green

# ── 2. SMS stub - functional code, gated by TWILIO_SMS_FROM env var ───────────
[System.IO.File]::WriteAllText("$PWD\app\services\sms.py", @'
"""
Twilio SMS send. Written but gated by TWILIO_SMS_FROM env var which is
intentionally unset in pilot - the function early-returns and the OTP
service treats SMS as unavailable.

When you set TWILIO_SMS_FROM to a real SMS-capable Twilio number, this
service starts working with no code change.

Cost note: SA SMS via Twilio is roughly R0.40 per send. At 100 patients/
month with ~1 OTP each that's R40/mo. Watch for retry storms - rate limit
is in place at the OTP layer but worth tracking.
"""
import os
import logging

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException

logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_SMS_FROM = os.environ.get("TWILIO_SMS_FROM")  # e.g. "+12025551234"


def is_sms_enabled() -> bool:
    """SMS is enabled iff TWILIO_SMS_FROM is set. Allows the OTP service
    to gracefully skip SMS as a channel option without raising."""
    return bool(TWILIO_SMS_FROM)


def send_sms(to_phone: str, body: str) -> tuple[bool, str | None]:
    """
    Send an SMS via Twilio. Returns (success, error_message).
    If TWILIO_SMS_FROM is unset, returns (False, "SMS not configured")
    without contacting Twilio.
    """
    if not is_sms_enabled():
        return False, "SMS not configured for this environment"

    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        return False, "Twilio credentials not configured"

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        message = client.messages.create(
            from_=TWILIO_SMS_FROM,
            to=to_phone,
            body=body,
        )
        logger.info(f"SMS sent to {to_phone} - SID: {message.sid}")
        return True, None
    except TwilioRestException as e:
        logger.error(f"Twilio SMS error: {e.code} - {e.msg}")
        return False, f"Twilio error: {e.msg}"
    except Exception as e:
        logger.error(f"Unexpected SMS send error: {e}")
        return False, f"Unexpected error: {e}"
'@)
Write-Host "  Created app/services/sms.py (gated by TWILIO_SMS_FROM)" -ForegroundColor Green

# ── 3. OTP delivery orchestration - picks the right service per channel ───────
[System.IO.File]::WriteAllText("$PWD\app\services\otp_delivery.py", @'
"""
Orchestrates OTP delivery across email / WhatsApp / SMS channels.

The OTP service (app.services.otp) generates and stores codes; this
module delivers them. Kept separate so the OTP storage logic stays
channel-agnostic.
"""
import logging
from app.services.otp import issue_code

logger = logging.getLogger(__name__)


def _email_body(code: str, app_name: str = "Phila") -> str:
    """Plain-text fallback. The Brevo send function also takes html_content
    if we want pretty formatting - skipping for pilot, plain text is fine."""
    return (
        f"Your {app_name} verification code is: {code}\n\n"
        f"This code expires in 5 minutes. If you didn't request this, "
        f"you can safely ignore this email."
    )


def _whatsapp_body(code: str) -> str:
    return (
        f"Your Phila verification code is: *{code}*\n\n"
        f"This code expires in 5 minutes."
    )


def _sms_body(code: str) -> str:
    """SMS - keep short, segments are charged per 160 chars."""
    return f"Phila code: {code}. Expires in 5 min."


def send_otp_via_email(email: str) -> tuple[bool, str | None]:
    """Generate code, store in Redis, send via Brevo. Returns (success, error)."""
    from app.services.email_brevo import send_email_with_attachment, send_email

    code = issue_code("email", email)
    body = _email_body(code)

    # Brevo's send_email is a simpler version without attachment
    # If your email_brevo.py only has send_email_with_attachment, we use
    # that with attachment=None
    try:
        success, error = send_email(
            to_email=email,
            to_name="",
            subject="Your Phila verification code",
            text_content=body,
        )
        return success, error
    except (ImportError, AttributeError):
        # email_brevo.py only has the attachment version
        success, error = send_email_with_attachment(
            to_email=email,
            to_name="",
            subject="Your Phila verification code",
            text_content=body,
            attachment_bytes=None,
            attachment_filename=None,
        )
        return success, error


def send_otp_via_whatsapp(phone: str) -> tuple[bool, str | None]:
    """Generate code, store in Redis, send via Twilio WhatsApp. Returns (success, error).

    This will fail in the Twilio sandbox unless the user has already joined
    the sandbox - by design. Outside-sandbox-via-business-sender will need
    Twilio's approved business WhatsApp sender, which is the path forward
    when WhatsApp OTP is enabled for real."""
    from app.services.whatsapp import send_whatsapp_message

    code = issue_code("whatsapp", phone)
    body = _whatsapp_body(code)

    try:
        success = send_whatsapp_message(phone, body)
        if success:
            return True, None
        return False, "WhatsApp send failed - patient may not be reachable on WhatsApp right now"
    except Exception as e:
        logger.error(f"WhatsApp OTP send to {phone} crashed: {e}")
        return False, f"Unexpected error: {e}"


def send_otp_via_sms(phone: str) -> tuple[bool, str | None]:
    """Generate code, store in Redis, send via Twilio SMS. Returns (success, error).
    Will gracefully fail with 'SMS not configured' until TWILIO_SMS_FROM is set."""
    from app.services.sms import send_sms, is_sms_enabled

    if not is_sms_enabled():
        return False, "SMS verification isn't available yet. Please use email or WhatsApp."

    code = issue_code("sms", phone)
    body = _sms_body(code)
    return send_sms(phone, body)
'@)
Write-Host "  Created app/services/otp_delivery.py" -ForegroundColor Green

# ── 4. Email service - confirm send_email helper exists or stub it ────────────
# email_brevo.py currently has send_email_with_attachment for documents.
# We need a simpler send_email for OTP. Check if it exists, add if not.
$brevoPath = "$PWD\app\services\email_brevo.py"
if (Test-Path $brevoPath) {
    $brevoContent = [System.IO.File]::ReadAllText($brevoPath)
    if ($brevoContent -notmatch "def send_email\b") {
        # Add a simpler send_email function alongside the existing one
        $addition = @'


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    text_content: str,
    html_content: str | None = None,
) -> tuple[bool, str | None]:
    """
    Simpler send for transactional emails without attachments (e.g. OTPs).
    Mirrors send_email_with_attachment but skips the attachment plumbing.
    Returns (success, error_message).
    """
    import logging
    logger = logging.getLogger(__name__)

    if not BREVO_API_KEY:
        return False, "Brevo not configured"

    try:
        import sib_api_v3_sdk
        from sib_api_v3_sdk.rest import ApiException
    except ImportError:
        return False, "Brevo SDK not installed"

    cfg = sib_api_v3_sdk.Configuration()
    cfg.api_key["api-key"] = BREVO_API_KEY
    api = sib_api_v3_sdk.TransactionalEmailsApi(sib_api_v3_sdk.ApiClient(cfg))

    payload = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email, "name": to_name or to_email}],
        sender={"email": BREVO_SENDER_EMAIL, "name": BREVO_SENDER_NAME},
        subject=subject,
        text_content=text_content,
        html_content=html_content,
    )

    try:
        api.send_transac_email(payload)
        logger.info(f"Brevo email sent to {to_email} - subject: {subject}")
        return True, None
    except ApiException as e:
        logger.error(f"Brevo API error: {e.status} {e.reason} (to {to_email})")
        return False, f"Brevo API error: {e.status} {e.reason}"
    except Exception as e:
        logger.error(f"Unexpected Brevo error: {e}")
        return False, f"Unexpected error: {e}"
'@
        Add-Content -Path $brevoPath -Value $addition
        Write-Host "  Added send_email function to email_brevo.py" -ForegroundColor Green
    } else {
        Write-Host "  email_brevo.py already has send_email - leaving untouched" -ForegroundColor Yellow
    }
} else {
    Write-Host "  WARN: app/services/email_brevo.py not found - OTP email will fail" -ForegroundColor Red
}

# ── 5. New routes - OTP request, verify, register-verified ────────────────────
[System.IO.File]::WriteAllText("$PWD\app\api\routes\otp_auth.py", @'
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


# ── Phone normalization (SA-specific for pilot) ───────────────────────────────
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


# ── Request models ────────────────────────────────────────────────────────────
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


# ── Helpers ───────────────────────────────────────────────────────────────────
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


# ── Routes ────────────────────────────────────────────────────────────────────
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
'@)
Write-Host "  Created app/api/routes/otp_auth.py" -ForegroundColor Green

# ── 6. Register the new router in main.py ─────────────────────────────────────
# Cant blind-rewrite main.py - find the line that includes other auth routers
# and add ours there.
$mainPath = "$PWD\app\main.py"
$mainContent = [System.IO.File]::ReadAllText($mainPath)

if ($mainContent -notmatch "from app\.api\.routes import.*otp_auth") {
    # Find the existing import of routes
    if ($mainContent -match "from app\.api\.routes import ([^\n]+)") {
        $oldImport = $matches[0]
        $importList = $matches[1].Trim()
        # Add otp_auth to the import list if not already
        if ($importList -notmatch "\botp_auth\b") {
            $newImport = "from app.api.routes import " + $importList + ", otp_auth"
            $mainContent = $mainContent.Replace($oldImport, $newImport)
            Write-Host "  Added otp_auth to routes import" -ForegroundColor Green
        }
    } else {
        Write-Host "  WARN: couldnt find routes import block in main.py - check manually" -ForegroundColor Yellow
    }

    # Add the include_router call - find the existing auth router include and add ours after it
    if ($mainContent -match "app\.include_router\(auth\.router[^)]*\)") {
        $authInclude = $matches[0]
        $newInclude = $authInclude + "`napp.include_router(otp_auth.router, prefix=`"/api/v1`")"
        $mainContent = $mainContent.Replace($authInclude, $newInclude)
        Write-Host "  Added otp_auth router include after auth.router" -ForegroundColor Green
    } else {
        Write-Host "  WARN: couldnt find auth.router include line - add otp_auth manually" -ForegroundColor Yellow
    }

    [System.IO.File]::WriteAllText($mainPath, $mainContent)
}

# ── 7. Make sure jwt and pyjwt are installed (jwt is the Python lib pyjwt) ────
# requirements.txt should already have it (used by core/security.py) but lets check
$reqPath = "$PWD\requirements.txt"
$reqContent = [System.IO.File]::ReadAllText($reqPath)
if ($reqContent -notmatch "(?i)pyjwt|^jwt") {
    Write-Host "  WARN: PyJWT may not be in requirements.txt - check core/security.py for jwt import" -ForegroundColor Yellow
}

# ── 8. Parse check ────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Parsing new Python files..." -ForegroundColor Cyan
python -c "import ast; ast.parse(open('app/services/otp.py').read()); print('OK: app/services/otp.py')"
python -c "import ast; ast.parse(open('app/services/sms.py').read()); print('OK: app/services/sms.py')"
python -c "import ast; ast.parse(open('app/services/otp_delivery.py').read()); print('OK: app/services/otp_delivery.py')"
python -c "import ast; ast.parse(open('app/api/routes/otp_auth.py').read()); print('OK: app/api/routes/otp_auth.py')"
python -c "import ast; ast.parse(open('app/main.py').read()); print('OK: app/main.py')"
python -c "import ast; ast.parse(open('app/services/email_brevo.py').read()); print('OK: app/services/email_brevo.py')"

if ($LASTEXITCODE -ne 0) {
    Write-Host "Parse failed somewhere - DO NOT push" -ForegroundColor Red
    exit 1
}

git add .
git commit -m "Phase 4b backend - OTP-verified patient registration. New POST /auth/otp/request issues a 6-digit code via email (live), WhatsApp (sandbox-limited), or SMS (TWILIO_SMS_FROM-gated, off by default). POST /auth/otp/verify validates code and returns 5-min JWT registration token. POST /auth/register-verified consumes the registration token, creates the user, runs the existing walk-in claim flow so manually-booked patients get their bookings/docs linked on signup. Codes stored in Redis with 5-min TTL, 3-attempt lockout, 3-requests-per-15min rate limit. Existing /auth/register and /auth/login untouched for backwards compat with the dashboard."
Write-Host ""
Write-Host "Phase 4b backend committed locally. Run git push when ready." -ForegroundColor Yellow