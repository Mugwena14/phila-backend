from datetime import date, time, datetime, timedelta
from typing import List
from sqlalchemy.orm import Session

from app.models.slot import Slot
from app.models.doctor import Doctor
from app.models.working_hours import WorkingHours

# Default hours used when no working_hours row exists for that day
DEFAULT_START = time(8, 0)
DEFAULT_END   = time(17, 0)

def generate_slots_for_day(
    db: Session,
    doctor: Doctor,
    target_date: date,
) -> List[Slot]:
    day_of_week = target_date.weekday()  # 0=Mon, 6=Sun

    working_hours = (
        db.query(WorkingHours)
        .filter(
            WorkingHours.doctor_id == doctor.id,
            WorkingHours.day_of_week == day_of_week,
            WorkingHours.is_active == True,
        )
        .first()
    )

    # ── Use default hours if none configured for this day ──────────
    if working_hours:
        start_time = working_hours.start_time
        end_time   = working_hours.end_time
    else:
        start_time = DEFAULT_START
        end_time   = DEFAULT_END
    # ───────────────────────────────────────────────────────────────

    # Check if slots already exist for this date
    existing = (
        db.query(Slot)
        .filter(Slot.doctor_id == doctor.id, Slot.date == target_date)
        .first()
    )
    if existing:
        return []

    # Generate slots
    slots = []
    duration = timedelta(minutes=doctor.slot_duration_minutes)

    start_dt = datetime.combine(target_date, start_time)
    end_dt   = datetime.combine(target_date, end_time)
    current  = start_dt

    while current + duration <= end_dt:
        slot = Slot(
            doctor_id=doctor.id,
            date=target_date,
            start_time=current.time(),
            end_time=(current + duration).time(),
            status="available",
        )
        db.add(slot)
        slots.append(slot)
        current += duration

    db.commit()
    return slots


def generate_slots_for_week(
    db: Session,
    doctor: Doctor,
    start_date: date,
) -> int:
    total = 0
    for i in range(7):
        target = start_date + timedelta(days=i)
        slots = generate_slots_for_day(db, doctor, target)
        total += len(slots)
    return total