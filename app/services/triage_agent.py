import anthropic
import json
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def triage_symptoms(symptoms: str, language: str = "English") -> dict:
    """
    Takes a patient's symptom description and returns:
    - Recommended specialty
    - Urgency level
    - Reasoning
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system="""You are a medical triage assistant for a South African private healthcare platform.

A patient has described their symptoms. Your job is to:
1. Determine the most appropriate medical specialty
2. Assess urgency level
3. Give a brief, clear explanation

Return ONLY valid JSON:
{
  "specialty": "exact specialty name e.g. General Practitioner, Cardiologist, Dermatologist",
  "urgency": "routine or soon or urgent or emergency",
  "urgency_explanation": "one sentence explaining urgency",
  "reasoning": "one sentence explaining specialty recommendation",
  "emergency_message": "only populated if urgency is emergency — what to tell patient"
}

Urgency levels:
- routine: can wait days or weeks
- soon: should see doctor within 2-3 days
- urgent: should see doctor today
- emergency: go to ER immediately — do NOT book an appointment

Common SA specialties to use:
General Practitioner, Cardiologist, Dermatologist, Paediatrician,
Gynaecologist, Orthopaedic Surgeon, Neurologist, Psychiatrist,
Ophthalmologist, ENT Specialist, Urologist, Gastroenterologist""",
            messages=[
                {
                    "role": "user",
                    "content": f"Patient symptoms: {symptoms}"
                }
            ]
        )

        return json.loads(response.content[0].text)

    except Exception as e:
        logger.error(f"Triage agent error: {e}")
        return {
            "specialty": "General Practitioner",
            "urgency": "routine",
            "urgency_explanation": "Please see a GP to assess your symptoms.",
            "reasoning": "A GP can assess and refer if needed.",
            "emergency_message": None,
        }