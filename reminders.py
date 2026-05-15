import os
import httpx

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_USER_ID"]
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

MORNING = (
    "Good morning! A few things to check in on:\n\n"
    "Did you drink water, make your bed, open the blinds, do your face routine, take your meds?\n"
    "What's your temp?\n"
    "Are you feeling like journaling today or reading?"
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


async def morning_msg() -> None:
    await send_tg(MORNING)


async def midday_msg() -> None:
    await send_tg(MIDDAY)


async def afternoon_msg() -> None:
    await send_tg(AFTERNOON)


async def evening_msg() -> None:
    await send_tg(EVENING)
