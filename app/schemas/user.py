from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime
from typing import Optional

class UserCreate(BaseModel):
    full_name: str
    email: EmailStr
    phone: str
    password: str
    language_pref: str = "en"
    role: str = "doctor"  # ← added

class UserResponse(BaseModel):
    id: UUID
    full_name: str
    email: str
    phone: str
    role: str
    created_at: datetime

    class Config:
        from_attributes = True

class Token(BaseModel):
    access_token: str
    token_type: str