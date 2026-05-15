import os
import httpx
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
from sqlalchemy import create_engine, text
from anthropic import AsyncAnthropic
from tavily import AsyncTavilyClient

engine = create_engine(os.environ["DATABASE_URL"])

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_USER_ID"]
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

anthropic_client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
tavily_client = AsyncTavilyClient(api_key=os.environ["TAVILY_API_KEY"])

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


# Conversation history (in-memory, resets on redeploy)
_history: list[dict] = []
MAX_HISTORY = 20  # keep last 10 exchanges

def _remember(role: str, content: str):
    _history.append({"role": role, "content": content})
    if len(_history) > MAX_HISTORY:
        del _history[:-MAX_HISTORY]


SEARCH_TRIGGERS = [
    "weather", "forecast", "temperature",
    "schedule", "mta", "bus", "train", "subway", "route",
    "news", "score", "price", "stock",
    "look up", "search for", "find me", "what's the", "what is the",
    "hours", "open", "when does",
]

def _needs_search(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SEARCH_TRIGGERS)

def _needs_reminder(text: str) -> bool:
    t = text.lower()
    return "remind" in t or "reminder" in t or "set a reminder" in t


async def handle_hey(text: str, scheduler) -> str:
    """Route 'hey' messages — search, reminder, or conversational."""

    # Reminder
    if _needs_reminder(text):
        reminder_sys = (
            "Extract the reminder time and task from this message. "
            "Respond ONLY in this format: REMINDER|HH:MM|task description (24-hour time). "
            "If no clear time is given, respond: REMINDER|NOTIME|task description."
        )
        decision = await call_claude(reminder_sys, text)
        if decision.startswith("REMINDER|"):
            parts = decision.split("|", 2)
            if len(parts) == 3:
                _, time_str, task = parts
                if time_str == "NOTIME":
                    return f"What time should I remind you to {task}?"
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
                    return "Couldn't parse the time — what time did you mean?"

    # Search
    if _needs_search(text):
        results = await tavily_client.search(text, search_depth="basic", max_results=3)
        answer = results.get("answer") or "\n".join(
            r["content"] for r in results.get("results", [])[:2]
        )
        summary_sys = "Summarize this search result for Lo in 2-3 sentences. Be direct and factual. No emojis."
        response = await call_claude(summary_sys, f"Question: {text}\nSearch results: {answer}")
        _remember("user", text)
        _remember("assistant", response)
        return response

    # Conversational — use full history so Claude remembers prior messages
    chat_sys = "You are Lo's personal assistant on Telegram. Respond conversationally — warm, brief, no emojis."
    _remember("user", text)
    messages = list(_history)
    r = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        system=chat_sys,
        messages=messages,
    )
    response = r.content[0].text.strip()
    _remember("assistant", response)
    return response
