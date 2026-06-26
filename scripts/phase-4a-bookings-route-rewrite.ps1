Write-Host "Phila Backend - rewrite create_walk_in_booking cleanly" -ForegroundColor Cyan

$path = "app\api\routes\bookings.py"
$content = [System.IO.File]::ReadAllText((Resolve-Path $path))

# Use a long unique multi-line anchor to find and replace the entire broken
# section. The anchor starts AT `try: notify_booking_confirmed(...)` (which
# is the last unchanged line before Phase 4a injected anything) and ends AT
# the closing `)` of `BookingDetailResponse(...)` in the original.
#
# Two replace targets - the file is currently in a broken half-edited state,
# so I match by what I can see in your latest paste.

# This is the broken block CURRENTLY in the file (from your paste)
$broken = @"
    try:
        notify_booking_confirmed(str(booking.id), db)
    except Exception as e:
        logger.warning(f"Walk-in notification failed (non-fatal): {e}")
    logger.info(f"Walk-in booking created: {booking.id} for {patient.full_name}")

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

    response = BookingDetailResponse(
        id=booking.id,
        patient_id=booking.patient_id,
        doctor_id=booking.doctor_id,
        slot_id=booking.slot_id,
        status=booking.status,
        reason=booking.reason,
        receptionist_note=booking.receptionist_note,
        risk_score=booking.risk_score,
        is_walk_in=True,
        created_at=booking.created_at,
        slot_date=str(slot.date)
    response.walk_in_message_sent = msg_sent
    response.walk_in_message_error = msg_error
    return response if slot else None,
        slot_start_time=str(slot.start_time) if slot else None,
        slot_end_time=str(slot.end_time) if slot else None,
        slot_duration_minutes=doctor.slot_duration_minutes,
        practice_name=doctor.practice_name,
        specialty=doctor.specialty,
        latitude=doctor.latitude,
        longitude=doctor.longitude,
        address=doctor.address,
        city=doctor.city if doctor else None,
        province=doctor.province if doctor else None,
    )
"@

# This is the clean replacement - based on the pre-Phase-4a structure
# (which works) with the Phase 4a logic woven in between
# `notify_booking_confirmed` and `return BookingDetailResponse(...)`.
$fixed = @"
    try:
        notify_booking_confirmed(str(booking.id), db)
    except Exception as e:
        logger.warning(f"Walk-in notification failed (non-fatal): {e}")
    logger.info(f"Walk-in booking created: {booking.id} for {patient.full_name}")

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

    return BookingDetailResponse(
        id=booking.id,
        patient_id=booking.patient_id,
        doctor_id=booking.doctor_id,
        slot_id=booking.slot_id,
        status=booking.status,
        reason=booking.reason,
        receptionist_note=booking.receptionist_note,
        risk_score=booking.risk_score,
        is_walk_in=True,
        created_at=booking.created_at,
        slot_date=str(slot.date) if slot else None,
        slot_start_time=str(slot.start_time) if slot else None,
        slot_end_time=str(slot.end_time) if slot else None,
        slot_duration_minutes=doctor.slot_duration_minutes,
        practice_name=doctor.practice_name,
        specialty=doctor.specialty,
        latitude=doctor.latitude,
        longitude=doctor.longitude,
        address=doctor.address,
        city=doctor.city if doctor else None,
        province=doctor.province if doctor else None,
        walk_in_message_sent=msg_sent,
        walk_in_message_error=msg_error,
    )
"@

if ($content.Contains($broken)) {
    $content = $content.Replace($broken, $fixed)
    [System.IO.File]::WriteAllText((Resolve-Path $path), $content)
    Write-Host "  Rewrote create_walk_in_booking cleanly" -ForegroundColor Green
} else {
    Write-Host "  Broken block not found exactly. Aborting before further damage." -ForegroundColor Red
    Write-Host "  Paste the current file state - the function may have drifted." -ForegroundColor Red
    exit 1
}

# Local parse check before pushing
Write-Host ""
Write-Host "Parsing app/api/routes/bookings.py..." -ForegroundColor Cyan
python -c "import ast; ast.parse(open('app/api/routes/bookings.py').read()); print('OK - parses cleanly')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Parse failed. Do not push. Inspect manually." -ForegroundColor Red
    exit 1
}

git add app\api\routes\bookings.py
git commit -m "Fix Phase 4a create_walk_in_booking - prior commits left the function with a mangled BookingDetailResponse(...) call (response.walk_in_message_* assignments were spliced INTO the constructor's argument list instead of after the return statement). This rewrites the function ending cleanly: send WhatsApp, write audit log, then a single return with walk_in_message_sent/walk_in_message_error passed as constructor kwargs instead of post-construction mutation. Local ast.parse confirmed clean."
Write-Host ""
Write-Host "Committed. Push to redeploy." -ForegroundColor Yellow