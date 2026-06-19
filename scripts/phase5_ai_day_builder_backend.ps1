Write-Host "Phase 5 - AI Day Builder backend endpoint" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path "app/routers" | Out-Null

Set-Content "app/routers/ai_day_builder.py" @'
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List
import os
import json
import anthropic

router = APIRouter(prefix="/ai", tags=["ai-day-builder"])

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a scheduling assistant for Phila Health.
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


class ParseDayRequest(BaseModel):
    text: str


class TimelineBlockOut(BaseModel):
    type: str
    title: str
    time: str
    duration_min: int


class ParseDayResponse(BaseModel):
    blocks: List[TimelineBlockOut]


@router.post("/parse-day", response_model=ParseDayResponse)
async def parse_day(payload: ParseDayRequest):
    if not payload.text or not payload.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": payload.text}],
        )
        raw_text = message.content[0].text.strip()

        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:].strip()

        parsed = json.loads(raw_text)
        blocks = parsed.get("blocks", [])

        if not blocks:
            raise HTTPException(
                status_code=422,
                detail="Could not parse any events from that description",
            )

        return ParseDayResponse(blocks=blocks)

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
Write-Host "  Created app/routers/ai_day_builder.py" -ForegroundColor Green

Write-Host ""
Write-Host "MANUAL STEP REQUIRED:" -ForegroundColor Yellow
Write-Host "Open app/main.py and add these two lines, matching how your" -ForegroundColor Yellow
Write-Host "other routers are registered (same prefix pattern):" -ForegroundColor Yellow
Write-Host ""
Write-Host "  from app.routers import ai_day_builder" -ForegroundColor White
Write-Host "  app.include_router(ai_day_builder.router, prefix=`"/api/v1`")" -ForegroundColor White
Write-Host ""
Write-Host "I am not editing main.py automatically to avoid breaking your" -ForegroundColor Yellow
Write-Host "existing router setup. Add it next to your other include_router calls." -ForegroundColor Yellow
Write-Host ""

git add .
git commit -m "Phase 5 - AI Day Builder backend: POST /ai/parse-day endpoint"
Write-Host "Backend script complete and committed!" -ForegroundColor Green