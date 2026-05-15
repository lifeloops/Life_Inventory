import os
import httpx
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, text
from anthropic import AsyncAnthropic

engine = create_engine(os.environ["DATABASE_URL"])

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_USER_ID"]
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

anthropic_client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ET = ZoneInfo("America/New_York")

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
    "Hey! How's movement going today?\n"
    "How does a shower reset walk sound?"
)

AFTERNOON = (
    "Start thinking about dinner!\n"
    "Also — have you drunk water today?"
)

EVENING = (
    "Evening check-in:\n\n"
    "Did you journal, eat at home, do your night routine?\n"
    "What were you grateful for today?"
)


async def send_tg(msg: str) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        await c.post(TG_URL, json={"chat_id": CHAT_ID, "text": msg})


def recent_ctx() -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT date, meds_taken, sleep_hours, social_media_goal,
                   gratitude, focus_activity
            FROM daily_logs
            WHERE date >= :start
            ORDER BY date DESC
        """), {"start": str(date.today() - timedelta(days=7))}).mappings().all()
    if not rows:
        return {}
    return {
        "last_gratitude": rows[0]["gratitude"],
        "last_sleep": rows[0]["sleep_hours"],
        "meds_streak_7d": sum(1 for r in rows if r["meds_taken"]),
        "screen_streak_7d": sum(1 for r in rows if r["social_media_goal"]),
        "sleep_avg_7d": round(
            sum((r["sleep_hours"] or 0) for r in rows) / len(rows), 1
        ),
    }


async def call_claude(system: str, user: str) -> str:
    r = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return r.content[0].text.strip()


async def morning_msg() -> None:
    ctx = recent_ctx()
    sys = (
        "Brief, warm good-morning for Lo. 1-2 sentences. Weave in recent "
        "data conversationally only when notable. No emojis, not saccharine."
    )
    greeting = await call_claude(sys, f"Context: {ctx}. Write the good morning.")
    await send_tg(greeting + MORNING_PROMPTS)


async def midday_msg() -> None:
    await send_tg(MIDDAY)


async def afternoon_msg() -> None:
    await send_tg(AFTERNOON)


async def evening_msg() -> None:
    await send_tg(EVENING)


async def goodnight_msg() -> None:
    ctx = recent_ctx()
    sys = (
        "You're messaging Lo at 10pm. Write two short things: "
        "1) A warm 1-2 sentence goodnight reflection based on today's data — only mention something if it's actually notable, otherwise just say goodnight simply. "
        "2) A casual prompt asking what's on her plate tomorrow, inviting her to brain dump. "
        "No emojis. Not saccharine. Conversational. "
        "End with: 'Just reply with hey to chat it through.'"
    )
    msg = await call_claude(sys, f"Today's data: {ctx}. Write the 10pm message.")
    await send_tg(msg)


async def handle_hey(text: str, scheduler) -> str:
    """Route 'hey' messages — schedule a reminder or respond conversationally."""
    sys = (
        "You are Lo's personal assistant on Telegram. "
        "If the message is asking to set a reminder, respond ONLY in this exact format: "
        "REMINDER|HH:MM|task description (24-hour time). "
        "Otherwise respond conversationally — warm, brief, no emojis."
    )
    response = await call_claude(sys, text)

    if response.startswith("REMINDER|"):
        parts = response.split("|", 2)
        if len(parts) == 3:
            _, time_str, task = parts
            try:
                hour, minute = map(int, time_str.strip().split(":"))
                now = datetime.now(ET)
                run_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if run_time > now:
                    scheduler.add_job(send_tg, "date", run_date=run_time, args=[f"Reminder: {task}"])
                    return f"Got it! Reminder set for {time_str} — {task}."
                else:
                    return "That time has already passed today."
            except ValueError:
                return "Couldn't parse the time for that reminder."

    return response
