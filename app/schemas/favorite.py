from pydantic import BaseModel
from uuid import UUID
from datetime import datetime


class FavoriteToggleResponse(BaseModel):
    favorited: bool
    doctor_id: UUID
