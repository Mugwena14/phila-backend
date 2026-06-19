Write-Host "Phase 6 - AI Week Builder backend (mode toggle on existing endpoint)" -ForegroundColor Cyan

Set-Content "app/routers/ai_day_builder.py" @'
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Dict
import os
import json
import anthropic

router = APIRouter(prefix="/ai", tags=["ai-day-builder"])

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT_DAY = """You are a scheduling assistant for Phila Health.
The user will describe how they want their day to go, in free text.
Convert their description into a structured JSON list of timeline blocks.

Each block must have:
- type: one of "workout", "meal", "task", "medication", "appointment"
- title: short title, e.g. "Morning Run", "Breakfast", "Call dentist"
- time: 24-hour format "HH:MM"
- duration_min: integer minutes. Your best estimate if not stated
  (default 30 for tasks and meals, 45-60 for workouts)

Rules:
- If the user gives a time range, calculate duration_min from it.
- If AM/PM is ambiguous (e.g. "6"), assume morning for wake/exercise
  activities and evening for dinner/wind-down activities.
- Sort blocks chronologically.
- Respond with ONLY valid JSON matching this exact shape, no markdown,
  no preamble:
{"blocks": [{"type": "...", "title": "...", "time": "HH:MM", "duration_min": 0}]}
"""

SYSTEM_PROMPT_WEEK = """You are a scheduling assistant for Phila Health.
The user will describe their typical week in free text - which days
follow which routine.

Convert their description into reusable day templates and assign them
across Monday through Sunday.

Output structure:
- templates: a list of 1-4 named templates, each with:
  - name: short descriptive name, e.g. "Gym Day", "Rest Day", "Weekend"
  - blocks: list of timeline blocks, each with:
    - type: one of "workout", "meal", "task", "medication", "appointment"
    - title: short title
    - time: 24-hour format "HH:MM"
    - duration_min: integer minutes, your best estimate if not stated
- week_assignments: an object mapping day index (Monday=0 through
  Sunday=6, as string keys "0" through "6") to a template name from
  your templates list. Every day 0 to 6 must be assigned to exactly
  one template name.

Rules:
- Re-use the same template across multiple days where the routine repeats.
- If the user does not mention a day, give it a sensible default
  (e.g. unmentioned weekend days get a relaxed "Rest Day" template).
- Sort blocks within each template chronologically.
- Respond with ONLY valid JSON, no markdown, no preamble, matching
  exactly this shape:
{"templates": [{"name": "...", "blocks": [{"type": "...", "title": "...", "time": "HH:MM", "duration_min": 0}]}], "week_assignments": {"0": "...", "1": "...", "2": "...", "3": "...", "4": "...", "5": "...", "6": "..."}}
"""


class ParseDayRequest(BaseModel):
    text: str
    mode: str = "day"


class TimelineBlockOut(BaseModel):
    type: str
    title: str
    time: str
    duration_min: int


@router.post("/parse-day")
async def parse_day(payload: ParseDayRequest):
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    is_week = payload.mode == "week"
    system_prompt = SYSTEM_PROMPT_WEEK if is_week else SYSTEM_PROMPT_DAY

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048 if is_week else 1024,
            system=system_prompt,
            messages=[{"role": "user", "content": payload.text}],
        )
        raw_text = message.content[0].text.strip()

        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()

        parsed = json.loads(raw_text)

        if is_week:
            templates = parsed.get("templates", [])
            week_assignments = parsed.get("week_assignments", {})
            if not templates:
                raise HTTPException(
                    status_code=422,
                    detail="Could not build any templates from that description",
                )
            return {"templates": templates, "week_assignments": week_assignments}
        else:
            blocks = parsed.get("blocks", [])
            if not blocks:
                raise HTTPException(
                    status_code=422,
                    detail="Could not parse any events from that description",
                )
            return {"blocks": blocks}

    except json.JSONDecodeError:
        raise HTTPException(
            status_code=502,
            detail="AI returned an unparseable response, please try rephrasing",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI parsing failed: {str(e)}")
'@
Write-Host "  Updated app/routers/ai_day_builder.py - mode toggle added" -ForegroundColor Green

git add .
git commit -m "Phase 6 - AI Week Builder backend: mode toggle generates templates + week assignments"
Write-Host "Backend script complete and committed!" -ForegroundColor Green