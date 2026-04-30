import anthropic
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def detect_crisis(message: str, patient_name: str) -> dict:
    """
    Analyses a patient message for crisis language.
    Called on every intake turn and every follow-up response.

    Returns:
        {
            "crisis_detected": bool,
            "severity": "none" | "low" | "high",
            "response_text": str | None  # What to send to patient if crisis
        }
    """
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=500,
            system="""You are a mental health safety monitor for a South African medical booking platform.

Your ONLY job is to detect if a patient message contains signs of:
- Suicidal ideation or self-harm
- Domestic abuse or violence
- Severe mental health crisis
- Expressions of hopelessness or wanting to die

You must respond in valid JSON only. No other text.

Return exactly this structure:
{
  "crisis_detected": true or false,
  "severity": "none" or "low" or "high",
  "reason": "brief explanation or empty string"
}

Rules:
- "high" severity: direct expressions of suicidal intent, self-harm, or immediate danger
- "low" severity: indirect expressions of hopelessness, feeling trapped, or concerning language
- "none": normal medical conversation, stress about illness, or general sadness about health
- Be sensitive but not over-reactive — a patient saying "my back is killing me" is NOT a crisis
- Context is a medical booking assistant in South Africa""",
            messages=[
                {
                    "role": "user",
                    "content": f"Patient name: {patient_name}\nPatient message: {message}"
                }
            ]
        )

        import json
        result = json.loads(response.content[0].text)

        # Build response text for high severity
        response_text = None
        if result.get("crisis_detected") and result.get("severity") == "high":
            response_text = (
                f"Hi {patient_name.split()[0]}, I hear you and I'm concerned about you. "
                f"Please know you're not alone. 💙\n\n"
                f"*SADAG Mental Health Crisis Line:* 0800 456 789 (free, 24/7)\n"
                f"*Lifeline South Africa:* 0861 322 322\n"
                f"*SMS:* 31393\n\n"
                f"If you're in immediate danger please call 10111.\n\n"
                f"Your doctor has been notified and will follow up with you."
            )
        elif result.get("crisis_detected") and result.get("severity") == "low":
            response_text = (
                f"Thank you for sharing that with me, {patient_name.split()[0]}. "
                f"Your doctor will be aware of how you're feeling. "
                f"If you ever need immediate support, SADAG is available 24/7 "
                f"on 0800 456 789. 💙"
            )

        return {
            "crisis_detected": result.get("crisis_detected", False),
            "severity": result.get("severity", "none"),
            "response_text": response_text,
        }

    except Exception as e:
        logger.error(f"Crisis detection error: {e}")
        # Fail safe — if detection fails, don't block the conversation
        return {
            "crisis_detected": False,
            "severity": "none",
            "response_text": None,
        }