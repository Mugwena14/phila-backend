import redis
import json
from app.core.config import settings

r = redis.from_url(settings.REDIS_URL, decode_responses=True)
TTL_SECONDS = 86400


def normalise_phone(phone: str) -> str:
    """Always store keys in +27XXXXXXXXX format."""
    cleaned = phone.strip().replace(" ", "").replace("-", "")
    # Remove whatsapp: prefix if present
    cleaned = cleaned.replace("whatsapp:", "")
    # Convert 0XXXXXXXXX → +27XXXXXXXXX
    if cleaned.startswith("0"):
        cleaned = "+27" + cleaned[1:]
    # Convert 27XXXXXXXXX → +27XXXXXXXXX
    elif cleaned.startswith("27") and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def get_conversation(phone: str) -> dict:
    key = f"intake:{normalise_phone(phone)}"
    data = r.get(key)
    if data:
        return json.loads(data)
    return {
        "phone": phone,
        "stage": "not_started",
        "messages": [],
        "booking_id": None,
        "turn": 0,
        "brief": None,
        "crisis_flagged": False,
    }


def save_conversation(phone: str, state: dict) -> None:
    key = f"intake:{normalise_phone(phone)}"
    r.setex(key, TTL_SECONDS, json.dumps(state))


def clear_conversation(phone: str) -> None:
    key = f"intake:{normalise_phone(phone)}"
    r.delete(key)


def get_conversation_by_booking(booking_id: str) -> dict | None:
    for key in r.scan_iter("intake:*"):
        data = r.get(key)
        if data:
            state = json.loads(data)
            if state.get("booking_id") == booking_id:
                return state
    return None