from fastapi import APIRouter
from pydantic import BaseModel
from app.services.triage_agent import triage_symptoms

router = APIRouter(prefix="/triage", tags=["triage"])


class TriageRequest(BaseModel):
    symptoms: str
    language: str = "English"


@router.post("/")
def triage(data: TriageRequest):
    result = triage_symptoms(data.symptoms, data.language)
    return result