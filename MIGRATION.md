# Migration — Reminder system + expanded tracked fields

This is a delta against the existing Life Inventory pipeline (see `CLAUDE.md` for full context). The pipeline is already live on Railway with the Telegram bot, FastAPI webhook, Postgres `habits` table, Google Sheets sync, and APScheduler all working. Do NOT rebuild from scratch — only apply these specific changes.

Read `CLAUDE.md` first, then inspect the current code to find where each change slots in. Adapt these snippets to match existing conventions in the codebase (import style, async patterns, error handling, logging).

---

## 1. Schema — add 7 columns to `habits`

`temperature_f` and `gratitude` already exist. Only these are new:

```sql
ALTER TABLE habits
  ADD COLUMN IF NOT EXISTS morning_routine TEXT,
  ADD COLUMN IF NOT EXISTS meds BOOLEAN,
  ADD COLUMN IF NOT EXISTS sleep_hours NUMERIC,
  ADD COLUMN IF NOT EXISTS social_media_goal BOOLEAN,
  ADD COLUMN IF NOT EXISTS focus_activity TEXT,
  ADD COLUMN IF NOT EXISTS movement_minutes INT,
  ADD COLUMN IF NOT EXISTS night_routine TEXT;
```

Run this in Railway's Postgres Query tab (Railway CLI is broken on Windows for this user). Verify with `\d habits` or `SELECT column_name FROM information_schema.columns WHERE table_name = 'habits';`.

---

## 2. Telegram parser — new prefix handlers

The webhook handler already parses `temp:` and `gratitude:`. Find that logic and extend it with these prefixes. Match existing style — if the current code uses a dict, add to the dict; if it uses if/elif, add branches.

```python
"morning":  lambda v: v.strip().lower(),                       # "y" or "water,bed,blinds,face"
"meds":     lambda v: v.strip().lower() in ("y", "yes"),
"sleep":    lambda v: float(v.strip()),
"screen":   lambda v: v.strip().lower() in ("y", "yes"),       # under 4 hr social media goal
"focus":    lambda v: v.strip().upper()[0],                    # "R" or "J"
"movement": lambda v: int("".join(c for c in v if c.isdigit()) or 0),
"night":    lambda v: v.strip().lower(),
```

Column mapping (prefix → DB column):

| Prefix | Column |
|---|---|
| `morning` | `morning_routine` |
| `meds` | `meds` |
| `sleep` | `sleep_hours` |
| `screen` | `social_media_goal` |
| `focus` | `focus_activity` |
| `movement` | `movement_minutes` |
| `night` | `night_routine` |

The existing upsert + real-time Sheets sync should pick these up automatically once columns exist and mapping is updated.

---

## 3. New file — `reminders.py`

Create this at the same level as `main.py`. Adjust imports if the project uses a different layout (e.g., `app/reminders.py`, `services/reminders.py`).

```python
import os
import httpx
from datetime import date, timedelta
from sqlalchemy import text
from anthropic import Anthropic
from db import engine  # adjust to actual engine import path

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


async def send_tg(msg: str) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(TG_URL, json={"chat_id": CHAT_ID, "text": msg})


def recent_ctx() -> dict:
    """Pull last 7 days of habits for personalization."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT date, meds, sleep_hours, social_media_goal,
                   gratitude, focus_activity
            FROM habits
            WHERE date >= :start
            ORDER BY date DESC
        """), {"start": date.today() - timedelta(days=7)}).mappings().all()
    if not rows:
        return {}
    return {
        "last_gratitude": rows[0]["gratitude"],
        "last_sleep": rows[0]["sleep_hours"],
        "meds_streak_7d": sum(1 for r in rows if r["meds"]),
        "screen_streak_7d": sum(1 for r in rows if r["social_media_goal"]),
        "sleep_avg_7d": round(
            sum((r["sleep_hours"] or 0) for r in rows) / len(rows), 1
        ),
    }


def call_claude(system: str, user: str) -> str:
    r = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text.strip()


MORNING_PROMPTS = (
    "\n\nLog your morning:\n"
    "morning: y       (water, bed, blinds, face)\n"
    "meds: y\n"
    "temp: 98.4\n"
    "sleep: 8\n"
    "screen: y        (under 4 hrs social media)\n"
    "focus: R         (R=reading, J=journaling)"
)

MIDDAY = (
    "3 PM check-in\n\n"
    "Remember: try to eat at home as much as possible.\n\n"
    "movement: 30"
)

EVENING = (
    "8 PM check-in\n\n"
    "Before smoking, think about how to decompress with intention at home.\n"
    "Try to finish eating 4 hrs before bed (by 8–8:30).\n\n"
    "gratitude: \n"
    "night: y"
)


async def morning_msg() -> None:
    ctx = recent_ctx()
    sys = (
        "Brief, warm good-morning for Lo. 1–2 sentences. Weave in recent "
        "data conversationally only when notable. No emojis, not saccharine."
    )
    greeting = call_claude(sys, f"Context: {ctx}. Write the good morning.")
    await send_tg(greeting + MORNING_PROMPTS)


async def midday_msg() -> None:
    await send_tg(MIDDAY)


async def evening_msg() -> None:
    await send_tg(EVENING)


async def goodnight_msg() -> None:
    ctx = recent_ctx()
    sys = (
        "Brief, warm good-night for Lo. 1–2 sentences reflecting on today "
        "if something's worth noting. Calming. No emojis, no advice."
    )
    msg = call_claude(sys, f"Today: {ctx}. Write the good night.")
    await send_tg(msg)
```

---

## 4. Register on the existing APScheduler instance

Find where the scheduler is currently set up (probably `main.py` or a `scheduler.py` — wherever the Sheets sync job is registered). Add these four jobs alongside the existing ones:

```python
from reminders import morning_msg, midday_msg, evening_msg, goodnight_msg

scheduler.add_job(morning_msg,   "cron", hour=9,  id="morning_msg")
scheduler.add_job(midday_msg,    "cron", hour=15, id="midday_msg")
scheduler.add_job(evening_msg,   "cron", hour=20, id="evening_msg")
scheduler.add_job(goodnight_msg, "cron", hour=22, id="goodnight_msg")
```

The scheduler is already timezone-aware (America/New_York) from prior work — do not re-initialize it.

---

## 5. Dependencies + env vars

Add to `requirements.txt` if not already present:

```
anthropic
httpx
```

Add to Railway env vars (Variables tab):

```
TELEGRAM_CHAT_ID=<personal chat id>
ANTHROPIC_API_KEY=<key from console.anthropic.com>
```

Both are single-line — no risk of the multi-line parsing issue that broke gspread auth earlier.

Get chat ID: send any message to the bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat":{"id":...}`.

---

## 6. Optional: debug endpoints for verification

Before waiting for the 9 AM cron to test, add temporary endpoints to fire each message on demand:

```python
from reminders import morning_msg, midday_msg, evening_msg, goodnight_msg

@app.get("/debug/morning")
async def _debug_morning():
    await morning_msg()
    return {"ok": True}

@app.get("/debug/midday")
async def _debug_midday():
    await midday_msg()
    return {"ok": True}

@app.get("/debug/evening")
async def _debug_evening():
    await evening_msg()
    return {"ok": True}

@app.get("/debug/goodnight")
async def _debug_goodnight():
    await goodnight_msg()
    return {"ok": True}
```

Hit each from a browser after deploy. Once all four send correctly to Telegram, remove these endpoints.

---

## Order of operations

1. Run SQL migration in Railway Postgres Query tab
2. Add parser entries + column mapping
3. Create `reminders.py`
4. Register scheduler jobs
5. Update `requirements.txt`, set env vars in Railway
6. Add debug endpoints, push to Railway
7. Hit each debug endpoint → verify Telegram delivery
8. Remove debug endpoints, final push
9. Wait for next 9 AM / 3 PM / 8 PM / 10 PM window to confirm cron firing
