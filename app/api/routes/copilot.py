from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
import anthropic

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
            max_tokens=1000,
            system=f"""You are a helpful AI assistant for a South African private medical practice.

You have access to real-time practice data provided below. Use it to answer questions accurately and helpfully.

Be conversational, concise, and professional. Format responses clearly — use bullet points for lists. Always refer to patients by name when available. Highlight crisis flags and urgent matters.

CURRENT PRACTICE DATA:
{data.context}""",
            messages=[{"role": m.role, "content": m.content} for m in data.messages],
        )
        return {"reply": response.content[0].text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))