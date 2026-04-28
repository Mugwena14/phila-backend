from datetime import date, time, datetime, timedelta
from typing import List
from sqlalchemy.orm import Session

from app.models.slot import Slot
from app.models.doctor import Doctor
from app.models.working_hours import WorkingHours

def generate_slots_for_day(
    db: Session,
    doctor: Doctor,
    target_date: date,
) -> List[Slot]:
    # Get working hours for this day of week
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

    if not working_hours:
        return []

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

    start_dt = datetime.combine(target_date, working_hours.start_time)
    end_dt = datetime.combine(target_date, working_hours.end_time)
    current = start_dt

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