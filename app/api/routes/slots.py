from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import date
from typing import List, Optional
from pydantic import BaseModel

from app.db.database import get_db
from app.models.slot import Slot
from app.models.user import User
from app.models.doctor import Doctor
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import logging

router = APIRouter(prefix="/slots", tags=["slots"])
security = HTTPBearer()
logger = logging.getLogger(__name__)


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


class BlockSlotRequest(BaseModel):
    reason: Optional[str] = "Blocked"


class SlotRangeRequest(BaseModel):
    start_date: str
    end_date: str
    reason: Optional[str] = "Blocked"


@router.patch("/{slot_id}/block", status_code=200)
def block_slot(
    slot_id: UUID,
    data: BlockSlotRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.status == "booked":
        raise HTTPException(status_code=400, detail="Cannot block a booked slot")

    slot.status = "blocked"
    slot.blocked_reason = data.reason
    slot.blocked_by = current_user.id
    db.commit()

    return {"message": "Slot blocked", "slot_id": str(slot_id)}


@router.patch("/{slot_id}/unblock", status_code=200)
def unblock_slot(
    slot_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    slot = db.query(Slot).filter(Slot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    if slot.status != "blocked":
        raise HTTPException(status_code=400, detail="Slot is not blocked")

    slot.status = "available"
    slot.blocked_reason = None
    slot.blocked_by = None
    db.commit()

    return {"message": "Slot unblocked", "slot_id": str(slot_id)}


@router.post("/block-range", status_code=200)
def block_slot_range(
    data: SlotRangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import datetime as dt
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    start = dt.strptime(data.start_date, "%Y-%m-%d").date()
    end   = dt.strptime(data.end_date,   "%Y-%m-%d").date()

    slots = db.query(Slot).filter(
        Slot.doctor_id == doctor.id,
        Slot.date >= start,
        Slot.date <= end,
        Slot.status == "available",
    ).all()

    for slot in slots:
        slot.status = "blocked"
        slot.blocked_reason = data.reason
        slot.blocked_by = current_user.id

    db.commit()
    logger.info(f"Blocked {len(slots)} slots from {data.start_date} to {data.end_date}")
    return {"blocked": len(slots)}


@router.post("/unblock-range", status_code=200)
def unblock_slot_range(
    data: SlotRangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from datetime import datetime as dt
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    start = dt.strptime(data.start_date, "%Y-%m-%d").date()
    end   = dt.strptime(data.end_date,   "%Y-%m-%d").date()

    slots = db.query(Slot).filter(
        Slot.doctor_id == doctor.id,
        Slot.date >= start,
        Slot.date <= end,
        Slot.status == "blocked",
    ).all()

    for slot in slots:
        slot.status = "available"
        slot.blocked_reason = None
        slot.blocked_by = None

    db.commit()
    logger.info(f"Unblocked {len(slots)} slots from {data.start_date} to {data.end_date}")
    return {"unblocked": len(slots)}