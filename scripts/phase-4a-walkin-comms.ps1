Write-Host "Phila Backend - Phase 4a - walk-in patient WhatsApp comms" -ForegroundColor Cyan

# ── 1. New model - booking_comms_log audit table ──────────────────────────────
[System.IO.File]::WriteAllText("$PWD\app\models\booking_comms_log.py", @'
"""
Audit log for booking-related comms (walk-in welcome WhatsApp, future
appointment reminders, etc).

Mirrors the document_send_log pattern. Why a separate table from
document_send_log: different domain (booking lifecycle vs document lifecycle),
different retention class under POPIA (booking comms = appointment record,
document comms = clinical record), different query patterns (most lookups
will be "what comms went out for this booking" not "for this document").

Why a separate table from notifications: notifications is the patient-facing
in-app feed (document_ready, appointment_reminder, etc). This table is the
server-side audit log of OUTBOUND messages we triggered, regardless of channel.
Mixing them would confuse the patient feed with practice operations.
"""
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
from app.db.database import Base


class BookingCommsLog(Base):
    __tablename__ = "booking_comms_log"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    booking_id = Column(UUID(as_uuid=True), ForeignKey("bookings.id"), nullable=False, index=True)

    # walkin_welcome | reminder | reschedule_notice | cancellation_notice
    comms_type = Column(String(64), nullable=False)

    channel = Column(String(32), nullable=False)        # 'whatsapp' | 'email' | 'sms'
    recipient = Column(String(256), nullable=False)     # phone or email
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    success = Column(Boolean, nullable=False)
    error_message = Column(Text, nullable=True)
    initiated_by = Column(UUID(as_uuid=True), nullable=True)  # receptionist/doctor user_id
'@)
Write-Host "  Created app/models/booking_comms_log.py" -ForegroundColor Green

# ── 2. Register the new model so autogenerate (and future migrations) see it ──
[System.IO.File]::WriteAllText("$PWD\app\models\__init__.py", @'
"""
Every SQLAlchemy model must be imported here. See env.py for the reasoning.
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
from app.models.document_send_log import DocumentSendLog
from app.models.booking_comms_log import BookingCommsLog
'@)
Write-Host "  Updated app/models/__init__.py to include BookingCommsLog" -ForegroundColor Green

# ── 3. Migration ──────────────────────────────────────────────────────────────
[System.IO.File]::WriteAllText("$PWD\alembic\versions\c4d8e2f7a9b1_add_booking_comms_log.py", @'
"""add booking comms log

Revision ID: c4d8e2f7a9b1
Revises: b2c8e1f4a5d6
Create Date: 2026-06-25
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c4d8e2f7a9b1"
down_revision: Union[str, Sequence[str], None] = "b2c8e1f4a5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_comms_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("comms_type", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("recipient", sa.String(length=256), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("initiated_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_booking_comms_log_booking_id", "booking_comms_log", ["booking_id"])


def downgrade() -> None:
    op.drop_index("ix_booking_comms_log_booking_id", table_name="booking_comms_log")
    op.drop_table("booking_comms_log")
'@)
Write-Host "  Created migration c4d8e2f7a9b1_add_booking_comms_log.py" -ForegroundColor Green

# ── 4. New service - composes and sends the walk-in welcome WhatsApp ──────────
[System.IO.File]::WriteAllText("$PWD\app\services\walkin_comms.py", @'
"""
Walk-in patient welcome comms.

Triggered after a receptionist creates a walk-in booking. Sends a WhatsApp
to the patient's real phone with:
  - Confirmation of the booking (practice + day + time)
  - One-paragraph pitch for the Phila app
  - Download link
  - Sign-up phone number (THE SAME PHONE) so the auth/register claim flow
    can find the WALKIN_+27... user and link the booking on signup

Returns (success, error_message). Either way, the call site writes to
booking_comms_log so the audit trail is complete.

Why not just inline this in the bookings route? Because the same comms
will fire on at least three other events in future phases (booking
created via app for non-Phila patient, booking rescheduled, etc) and
keeping the message text + send logic in one file means we only edit
one place to change the copy.
"""
import logging
from datetime import date, time

from app.services.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)

# Hardcoded for now. Replace when the marketing site / app store URLs exist.
PHILA_DOWNLOAD_URL = "https://philahealth.co.za/app"


def _format_appointment_day(d: date) -> str:
    """Friday, 27 June format - readable on WhatsApp without a year (since
    it's near-term and the year is contextually obvious)."""
    return d.strftime("%A, %d %B")


def _format_appointment_time(t: time) -> str:
    """09:30 format."""
    return t.strftime("%H:%M")


def build_walkin_message(
    patient_name: str,
    practice_name: str,
    appointment_date: date,
    appointment_time: time,
    patient_phone: str,
) -> str:
    """
    Compose the walk-in welcome WhatsApp body. Single source of truth for the
    message copy - change here if you want to tweak wording, not in the route.
    """
    day = _format_appointment_day(appointment_date)
    t = _format_appointment_time(appointment_time)

    return (
        f"Hi {patient_name}, {practice_name} has booked you in for "
        f"{day} at {t}.\n\n"
        f"Phila is the app behind this booking - it's also a full health "
        f"companion that tracks your water, sleep, mood, workouts, and meals, "
        f"plans your day with a built-in task timeline, holds all your doctor's "
        f"notes and prescriptions in one place, and lets you book your next "
        f"visit in seconds.\n\n"
        f"Download Phila: {PHILA_DOWNLOAD_URL}\n"
        f"Sign up with this phone number ({patient_phone}) so we can link your "
        f"booking to your account."
    )


def send_walkin_welcome(
    patient_phone: str,
    patient_name: str,
    practice_name: str,
    appointment_date: date,
    appointment_time: time,
) -> tuple[bool, str | None]:
    """
    Send the walk-in welcome WhatsApp. Returns (success, error_message).
    Wraps the existing whatsapp service so the bookings route doesn't need
    to know how Twilio is wired.
    """
    body = build_walkin_message(
        patient_name=patient_name,
        practice_name=practice_name,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        patient_phone=patient_phone,
    )

    try:
        success = send_whatsapp_message(patient_phone, body)
        if success:
            return True, None
        return False, "Twilio rejected the send (see backend logs)"
    except Exception as e:
        logger.error(f"Walk-in welcome send to {patient_phone} crashed: {e}")
        return False, f"Unexpected error: {e}"
'@)
Write-Host "  Created app/services/walkin_comms.py" -ForegroundColor Green

# ── 5. Extend BookingDetailResponse with the two new optional fields ──────────
# We do this with a targeted str_replace rather than a full rewrite because
# the schemas file is large and the rest of it is stable.
$bookingSchemaPath = "$PWD\app\schemas\booking.py"
$content = [System.IO.File]::ReadAllText($bookingSchemaPath)

$oldDetail = "class BookingDetailResponse(BookingResponse):"
$newDetail = @'
class BookingDetailResponse(BookingResponse):
    # Phase 4a - walk-in welcome WhatsApp delivery result, populated only on
    # POST /bookings/walk-in. Null on all other booking-fetch endpoints.
    walk_in_message_sent: Optional[bool] = None
    walk_in_message_error: Optional[str] = None
'@

if ($content -match [regex]::Escape($oldDetail)) {
    $content = $content -replace [regex]::Escape($oldDetail), $newDetail
    [System.IO.File]::WriteAllText($bookingSchemaPath, $content)
    Write-Host "  Extended BookingDetailResponse with walk_in_message_* fields" -ForegroundColor Green
} else {
    Write-Host "  WARN: BookingDetailResponse anchor not found - inspect $bookingSchemaPath manually" -ForegroundColor Yellow
}

# ── 6. Extend the walk-in route to send WhatsApp + log to audit table ─────────
# This needs to be a targeted edit not a full rewrite because we havent seen
# the full bookings.py file - too risky to rewrite blind.
$bookingsRoutePath = "$PWD\app\api\routes\bookings.py"
$bookingsContent = [System.IO.File]::ReadAllText($bookingsRoutePath)

# Add imports at top if not already there
if ($bookingsContent -notmatch "from app.services.walkin_comms") {
    $importBlock = @'

# Phase 4a - walk-in welcome comms
from app.services.walkin_comms import send_walkin_welcome
from app.models.booking_comms_log import BookingCommsLog
'@
    # Insert after the last "from app." import line - safer than guessing line numbers
    $lastAppImport = ($bookingsContent | Select-String -Pattern "^from app\." -AllMatches).Matches | Select-Object -Last 1
    if ($lastAppImport) {
        $insertAt = $bookingsContent.IndexOf("`n", $lastAppImport.Index)
        $bookingsContent = $bookingsContent.Insert($insertAt, $importBlock)
        Write-Host "  Added imports for walkin_comms and BookingCommsLog" -ForegroundColor Green
    } else {
        Write-Host "  WARN: could not find anchor to insert imports - add manually at top of bookings.py" -ForegroundColor Yellow
    }
}

# Now extend the walk-in route. Find the final `return ...` for create_walk_in_booking
# and inject the comms call + audit log + response population before it.
#
# The route currently ends with something like:
#   return BookingDetailResponse(
#       id=booking.id,
#       ...
#       is_walk_in=True,
#       created_at=booking.created_at,
#       slot_date=...,
#       slot_start_time=...,
#       slot_end_time=...,
#       slot_duration_minutes=...,
#   )
#
# We need to capture that response in a variable first, then send WhatsApp,
# write audit log, mutate the response, return it. Cleanest pattern is to
# find the `return BookingDetailResponse(` inside create_walk_in_booking
# specifically and replace it.

# Heuristic: find the create_walk_in_booking function block, then within it
# the first `return BookingDetailResponse(` line.
$walkInFnPattern = "(?s)def create_walk_in_booking\(.*?(return BookingDetailResponse\([^)]*\))"
if ($bookingsContent -match $walkInFnPattern) {
    $oldReturn = $matches[1]

    # Build the new block. Note: relies on `booking`, `patient`, `doctor`,
    # `slot`, `current_user`, and `data` being in scope - all are based on
    # the earlier view of the route. If the route has been refactored since
    # we read it, this will fail loudly (the str_replace below wont find the
    # anchor) and youll see the warning.
    $newBlock = @"
# Phase 4a - send walk-in welcome WhatsApp to the patient's real phone.
    # data.patient_phone is the un-prefixed real number; patient.phone is
    # the WALKIN_+27... form. Send to the real number.
    practice_name = doctor.practice_name or "your doctor"
    appointment_date = slot.date if slot else None
    appointment_time = slot.start_time if slot else None

    msg_sent = False
    msg_error: str | None = None
    if appointment_date and appointment_time:
        msg_sent, msg_error = send_walkin_welcome(
            patient_phone=data.patient_phone,
            patient_name=data.patient_name,
            practice_name=practice_name,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
        )
    else:
        msg_error = "Slot has no date/time - cannot compose appointment message"

    # Audit log entry, regardless of success or failure
    db.add(BookingCommsLog(
        booking_id=booking.id,
        comms_type="walkin_welcome",
        channel="whatsapp",
        recipient=data.patient_phone,
        success=msg_sent,
        error_message=msg_error,
        initiated_by=current_user.id,
    ))
    db.commit()

    response = $oldReturn
    response.walk_in_message_sent = msg_sent
    response.walk_in_message_error = msg_error
    return response
"@

    # Replace - the regex match captured the `return BookingDetailResponse(...)` call.
    # We want to replace JUST that return with our new block. Use string Replace
    # to avoid regex backreference complexity.
    $bookingsContent = $bookingsContent.Replace($oldReturn, $newBlock)
    [System.IO.File]::WriteAllText($bookingsRoutePath, $bookingsContent)
    Write-Host "  Extended create_walk_in_booking with WhatsApp send + audit log" -ForegroundColor Green
} else {
    Write-Host "  WARN: could not find create_walk_in_booking return pattern - inspect manually" -ForegroundColor Yellow
    Write-Host "  The function may have been refactored since we last read it." -ForegroundColor Yellow
}

# ── 7. Commit (no push yet - migration runs BEFORE push via Railway CLI) ──────
git add .
git commit -m "Phase 4a - walk-in patient welcome WhatsApp. When a receptionist creates a walk-in booking via POST /bookings/walk-in, the patient receives a WhatsApp on their real phone with the booking details, an explanation of what Phila does, the download link, and instructions to sign up with the same phone number so the auth/register claim flow links the booking to their new account. New booking_comms_log table audits every send attempt for POPIA defensibility. BookingDetailResponse gains walk_in_message_sent/walk_in_message_error so the dashboard receptionist sees a real-time toast about whether the WhatsApp landed. Message text in app/services/walkin_comms.py - single source of truth for the copy."
Write-Host ""
Write-Host "Phase 4a backend code committed locally." -ForegroundColor Yellow
Write-Host ""
Write-Host "NEXT STEPS - migration must run BEFORE Railway redeploys:" -ForegroundColor Cyan
Write-Host "  1. railway run alembic upgrade head    (applies c4d8e2f7a9b1 to prod)" -ForegroundColor Cyan
Write-Host "  2. git push                            (Railway redeploys, env.py confirms 'already at head')" -ForegroundColor Cyan
Write-Host "  3. Paste the deploy log so we can verify clean state" -ForegroundColor Cyan