import anthropic
import json
from app.core.config import settings
from app.models.patient_profile import PatientProfile
from app.models.user import User
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

# SA preventive care guidelines
GUIDELINES = """
South African Preventive Care Guidelines:
- Blood pressure check: every year for adults 18+
- Cholesterol check: every 5 years for adults 35+ (every year if high risk)
- Diabetes screening: every 3 years for adults 45+ (earlier if overweight)
- Pap smear: every 3 years for women 25-65
- Mammogram: every 2 years for women 40+
- Prostate check: every year for men 50+ (45+ if high risk)
- Eye test: every 2 years for adults 40+
- Dental checkup: every 6 months
- HIV test: every year for sexually active adults
- Flu vaccine: every year
- Mental health screening: any adult with chronic stress or mood changes
"""


def detect_care_gaps(
    patient_id: str,
    patient_name: str,
    age: int,
    gender: str,
    visit_history: list,
    last_visit_date: str | None,
) -> list:
    """
    Uses Claude to detect care gaps based on patient history
    and SA preventive care guidelines.
    Returns list of gap objects.
    """
    try:
        history_text = json.dumps(visit_history[-10:], indent=2) if visit_history else "No visits on record"
        current_year = datetime.now().year

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            system=f"""You are a preventive healthcare advisor for a South African medical platform.

Using the SA preventive care guidelines below, identify care gaps for this patient.

{GUIDELINES}

Return ONLY valid JSON — no markdown, no backticks:
{{
  "gaps": [
    {{
      "type": "name of screening e.g. Blood pressure check",
      "reason": "one sentence why this patient needs it",
      "urgency": "overdue or due_soon",
      "message": "warm personalised WhatsApp message to send patient (max 2 sentences)"
    }}
  ]
}}

Rules:
- Maximum 2 gaps per scan — pick the most important ones
- Only flag gaps relevant to the patient's age and gender
- Be specific and warm — not clinical or scary
- If no gaps detected return {{"gaps": []}}
- today's year is {current_year}""",
            messages=[
                {
                    "role": "user",
                    "content": f"""Patient: {patient_name}
Age: {age}
Gender: {gender}
Last visit: {last_visit_date or 'Never'}
Visit history: {history_text}

Identify the most important care gaps for this patient."""
                }
            ]
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)
        return result.get("gaps", [])

    except Exception as e:
        logger.error(f"Care gap detection error for {patient_id}: {e}")
        return []