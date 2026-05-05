from pydantic import BaseModel, ConfigDict
from typing import Optional, Any, Dict
from datetime import datetime
from uuid import UUID  # Import this

class NotificationResponse(BaseModel):
    # Change these from str to UUID
    id: UUID
    user_id: UUID
    type: str
    title: str
    body: str
    is_read: bool
    action_type: Optional[str] = None
    action_data: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UnreadCountResponse(BaseModel):
    count: int