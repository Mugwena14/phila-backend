import anthropic
import json
from app.core.config import settings
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# Common chronic medication durations in SA
MEDICATION_DURATIONS = {
    "metformin": 30,
    "amlodipine": 30,
    "losartan": 30,
    "atorvastatin": 90,
    "simvastatin": 90,
    "lisinopril": 30,
    "atenolol": 30,
    "hydrochlorothiazide": 30,
    "aspirin": 90,
    "warfarin": 30,
    "levothyroxine": 90,
    "omeprazole": 30,
    "pantoprazole": 30,
    "salbutamol": 30,
    "fluticasone": 30,
    "insulin": 30,
    "methotrexate": 30,
}


def estimate_refill_date(
    medication_name: str,
    last_prescribed_date: str,
) -> str | None:
    """
    Estimates when a patient will need a refill
    based on medication name and last prescribed date.
    """
    try:
        last_date = datetime.strptime(last_prescribed_date, "%Y-%m-%d")

        # Check known medications first
        med_lower = medication_name.lower()
        duration_days = None
        for known_med, days in MEDICATION_DURATIONS.items():
            if known_med in med_lower:
                duration_days = days
                break

        # Default to 30 days if unknown
        if not duration_days:
            duration_days = 30

        refill_date = last_date + timedelta(days=duration_days)
        return refill_date.strftime("%Y-%m-%d")

    except Exception as e:
        logger.error(f"Error estimating refill date: {e}")
        return None


def extract_medications_from_brief(brief_text: str) -> list:
    """
    Uses Claude to extract chronic medications from an intake brief.
    Returns list of medication names.
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            system="""Extract chronic medications from this medical intake brief.

Return ONLY valid JSON — no markdown:
{
  "chronic_medications": ["medication1", "medication2"]
}

Rules:
- Only include medications taken regularly (not once-off)
- Include common SA chronic meds: blood pressure, diabetes, cholesterol, asthma, thyroid
- Return empty array if none found
- Normalise names to generic form e.g. "Disprin" → "aspirin" """,
            messages=[
                {"role": "user", "content": f"Intake brief: {brief_text}"}
            ]
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        result = json.loads(text.strip())
        return result.get("chronic_medications", [])

    except Exception as e:
        logger.error(f"Medication extraction error: {e}")
        return []