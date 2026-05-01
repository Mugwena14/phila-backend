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


class BlockRangeRequest(BaseModel):
    date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
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
def block_range(
    data: BlockRangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get doctor profile
    doctor = db.query(Doctor).filter(
        Doctor.user_id == current_user.id
    ).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    from datetime import datetime
    target_date = datetime.strptime(data.date, "%Y-%m-%d").date()

    query = db.query(Slot).filter(
        Slot.doctor_id == doctor.id,
        Slot.date == target_date,
        Slot.status == "available",
    )

    if data.start_time:
        from datetime import time
        start = datetime.strptime(data.start_time, "%H:%M").time()
        query = query.filter(Slot.start_time >= start)

    if data.end_time:
        from datetime import time
        end = datetime.strptime(data.end_time, "%H:%M").time()
        query = query.filter(Slot.start_time <= end)

    slots = query.all()
    count = 0

    for slot in slots:
        slot.status = "blocked"
        slot.blocked_reason = data.reason
        slot.blocked_by = current_user.id
        count += 1

    db.commit()

    return {"message": f"{count} slots blocked", "date": data.date}