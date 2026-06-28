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