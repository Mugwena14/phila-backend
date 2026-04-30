import redis
import json
from app.core.config import settings

# Single Redis client reused across all calls
r = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Conversation expires after 24 hours of inactivity
TTL_SECONDS = 86400


def get_conversation(phone: str) -> dict:
    """
    Load conversation state for a patient phone number.
    Returns empty state if no conversation exists.
    """
    key = f"intake:{phone}"
    data = r.get(key)
    if data:
        return json.loads(data)
    return {
        "phone": phone,
        "stage": "not_started",
        "messages": [],        # Full Claude conversation history
        "booking_id": None,
        "turn": 0,
        "brief": None,         # Populated when intake is complete
        "crisis_flagged": False,
    }


def save_conversation(phone: str, state: dict) -> None:
    """Save conversation state with 24hr TTL."""
    key = f"intake:{phone}"
    r.setex(key, TTL_SECONDS, json.dumps(state))


def clear_conversation(phone: str) -> None:
    """Clear conversation after intake is complete."""
    key = f"intake:{phone}"
    r.delete(key)


def get_conversation_by_booking(booking_id: str) -> dict | None:
    """
    Find conversation state by booking ID.
    Used by follow-up agent to check if intake was completed.
    """
    # Scan all intake keys — only used occasionally so cost is fine
    for key in r.scan_iter("intake:*"):
        data = r.get(key)
        if data:
            state = json.loads(data)
            if state.get("booking_id") == booking_id:
                return state
    return None