from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List
from datetime import datetime
import asyncio
import json
import logging

from app.db.database import get_db
from app.models.user import User
from app.models.doctor import Doctor
from app.models.booking import Booking
from app.models.slot import Slot
from app.core.security import decode_token
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/waiting-room", tags=["waiting-room"])
security = HTTPBearer()
logger = logging.getLogger(__name__)

# ── SSE broadcast manager ────────────────────────────────────────────────────
class WaitingRoomManager:
    def __init__(self):
        self.connections: List[asyncio.Queue] = []

    async def connect(self, queue: asyncio.Queue):
        self.connections.append(queue)
        logger.info(f"Waiting room client connected. Total: {len(self.connections)}")

    async def disconnect(self, queue: asyncio.Queue):
        if queue in self.connections:
            self.connections.remove(queue)
        logger.info(f"Waiting room client disconnected. Total: {len(self.connections)}")

    async def broadcast(self, data: dict):
        logger.info(f"Broadcasting to {len(self.connections)} clients")
        dead = []
        for queue in self.connections:
            try:
                await queue.put(data)
            except Exception:
                dead.append(queue)
        for q in dead:
            await self.disconnect(q)

manager = WaitingRoomManager()
# ─────────────────────────────────────────────────────────────────────────────


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


def get_queue_state(db: Session, doctor_id) -> dict:
    """Build current queue state for the waiting room display."""
    today = datetime.today().date()

    bookings = (
        db.query(Booking)
        .join(Slot, Booking.slot_id == Slot.id)
        .filter(
            Booking.doctor_id == doctor_id,
            Slot.date == today,
            Booking.status.in_(["confirmed", "arrived", "in_consultation"]),
        )
        .order_by(Slot.start_time)
        .all()
    )

    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    practice_name = doctor.practice_name if doctor else "Phila Medical"

    now_seen = None
    queue = []

    for b in bookings:
        patient = db.query(User).filter(User.id == b.patient_id).first()
        if not patient:
            continue

        name = patient.full_name
        parts = name.strip().split()
        display_name = f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else parts[0]

        slot = db.query(Slot).filter(Slot.id == b.slot_id).first()
        slot_time = str(slot.start_time)[:5] if slot else "--:--"

        entry = {
            "id": str(b.id),
            "display_name": display_name,
            "status": b.status,
            "slot_time": slot_time,
        }

        if b.status == "in_consultation":
            now_seen = entry
        else:
            queue.append(entry)

    return {
        "practice_name": practice_name,
        "now_seen": now_seen,
        "queue": queue,
        "total_waiting": len(queue),
        "timestamp": datetime.now().isoformat(),
    }


@router.get("/queue")
def get_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get current queue state — used for initial dashboard load."""
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")
    return get_queue_state(db, doctor.id)


@router.get("/public-queue/{doctor_id}")
def get_public_queue(doctor_id: str, db: Session = Depends(get_db)):
    """Public endpoint — no auth — used by the waiting room display."""
    from uuid import UUID
    try:
        did = UUID(doctor_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid doctor ID")

    doctor = db.query(Doctor).filter(Doctor.id == did).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    return get_queue_state(db, did)


@router.post("/call-next")
async def call_next_patient(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Doctor triggers next patient.
    - Current in_consultation → completed
    - Next confirmed/arrived → in_consultation
    - Broadcasts updated queue to all SSE clients
    """
    doctor = db.query(Doctor).filter(Doctor.user_id == current_user.id).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor profile not found")

    today = datetime.today().date()

    # Complete whoever is currently in consultation
    current = (
        db.query(Booking)
        .join(Slot, Booking.slot_id == Slot.id)
        .filter(
            Booking.doctor_id == doctor.id,
            Slot.date == today,
            Booking.status == "in_consultation",
        )
        .first()
    )
    if current:
        current.status = "completed"
        current.completed_at = datetime.now()
        logger.info(f"Completed booking {current.id}")

    # Find next confirmed or arrived patient
    next_booking = (
        db.query(Booking)
        .join(Slot, Booking.slot_id == Slot.id)
        .filter(
            Booking.doctor_id == doctor.id,
            Slot.date == today,
            Booking.status.in_(["confirmed", "arrived"]),
        )
        .order_by(Slot.start_time)
        .first()
    )

    if not next_booking:
        db.commit()
        queue_state = get_queue_state(db, doctor.id)
        await manager.broadcast(queue_state)
        return {"message": "No more patients in queue", "queue": queue_state}

    next_booking.status = "in_consultation"
    next_booking.arrived_at = datetime.now()
    db.commit()

    patient = db.query(User).filter(User.id == next_booking.patient_id).first()
    logger.info(f"Calling next: {patient.full_name if patient else 'Unknown'}")

    queue_state = get_queue_state(db, doctor.id)

    # Push to all SSE connections
    await manager.broadcast(queue_state)

    return {
        "message": f"Called next patient",
        "now_seen": queue_state["now_seen"],
        "remaining": queue_state["total_waiting"],
    }


@router.get("/stream/{doctor_id}")
async def stream_queue(doctor_id: str, db: Session = Depends(get_db)):
    """
    SSE stream — waiting room display connects here.
    Sends queue state on connect + on every call-next trigger.
    """
    from uuid import UUID
    try:
        did = UUID(doctor_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid doctor ID")

    doctor = db.query(Doctor).filter(Doctor.id == did).first()
    if not doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")

    queue: asyncio.Queue = asyncio.Queue()
    await manager.connect(queue)

    # Send initial state immediately on connect
    initial = get_queue_state(db, did)

    async def event_generator():
        try:
            # Send initial state
            yield f"data: {json.dumps(initial)}\n\n"

            while True:
                try:
                    # Wait for next update (with timeout to send keepalive)
                    data = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    # Keepalive ping every 25s to prevent connection drop
                    yield f": keepalive\n\n"

        except asyncio.CancelledError:
            pass
        finally:
            await manager.disconnect(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )