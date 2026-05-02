from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
import anthropic
import json
import re

from app.db.database import get_db
from app.models.user import User
from app.core.security import decode_token
from app.core.config import settings
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/copilot", tags=["copilot"])
security = HTTPBearer()
client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


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


class ChatMessage(BaseModel):
    role: str
    content: str


class CopilotRequest(BaseModel):
    messages: List[ChatMessage]
    context: str


@router.post("/chat")
def copilot_chat(
    data: CopilotRequest,
    current_user: User = Depends(get_current_user),
):
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=f"""You are an AI assistant for a South African private medical practice. You have access to real-time practice data and can perform actions on behalf of the doctor or receptionist.

CURRENT PRACTICE DATA:
{data.context}

RESPONSE FORMAT:
You must ALWAYS respond with a valid JSON object with this exact structure:
{{
  "reply": "Your conversational response here",
  "action": null
}}

OR if the user is requesting an action:
{{
  "reply": "Your conversational response confirming what you are about to do",
  "action": {{
    "type": "block_slots",
    "description": "Human readable description of what will happen",
    "params": {{}}
  }}
}}

AVAILABLE ACTION TYPES:
1. block_slots — Block slots for a specific date or day of week
   params: {{ "date": "YYYY-MM-DD" }} OR {{ "day_of_week": "Saturday", "weeks_ahead": 4 }}, plus "reason": "string"
   
2. unblock_slots — Unblock all slots for a specific date
   params: {{ "date": "YYYY-MM-DD" }}

3. update_booking_status — Update status of bookings
   params: {{ "date": "YYYY-MM-DD", "status": "completed" }} for bulk by date
   OR {{ "booking_id": "uuid", "status": "arrived" }} for single booking
   Valid statuses: confirmed, arrived, in_consultation, completed, no_show, cancelled

4. cancel_booking — Cancel a specific booking
   params: {{ "booking_id": "uuid" }}

RULES:
- Only output an action if the user is clearly requesting one
- For read-only questions (who, what, how many, show me), set action to null
- Always explain what the action will do in the reply before the user confirms
- Use today's date context from the practice data
- Be conversational, concise, and professional
- Never output anything outside the JSON object""",
            messages=[{"role": m.role, "content": m.content} for m in data.messages],
        )

        raw = response.content[0].text.strip()

        # Strip markdown code fences if present
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'^```\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)

        parsed = json.loads(raw)
        return {
            "reply": parsed.get("reply", "Sorry, I could not process that."),
            "action": parsed.get("action", None),
        }

    except json.JSONDecodeError:
        # Fallback if Claude doesn't return valid JSON
        return {
            "reply": response.content[0].text if response else "Sorry, I ran into an issue.",
            "action": None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))