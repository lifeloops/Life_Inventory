import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from reminders import morning_msg, midday_msg, afternoon_msg, evening_msg, goodnight_msg, handle_hey

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import httpx
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Telegram config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# MyFitnessPal config
MFP_USERNAME = os.getenv("MFP_USERNAME")
MFP_PASSWORD = os.getenv("MFP_PASSWORD")

# Google Sheets config
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# =====================
# DATABASE MODELS
# =====================

class DailyLog(Base):
    __tablename__ = "daily_logs"
    
    id = Column(Integer, primary_key=True)
    date = Column(String, unique=True, index=True)  # YYYY-MM-DD
    
    # Morning habits (9am)
    water_morning = Column(Boolean, default=False)
    bed = Column(Boolean, default=False)
    blinds = Column(Boolean, default=False)
    face_routine_morning = Column(Boolean, default=False)
    meds_taken = Column(Boolean, default=False)
    
    # Afternoon (5pm)
    t_break = Column(Boolean, default=None)  # None = not logged yet
    
    # Evening habits (8pm)
    journaling = Column(Boolean, default=False)
    eat_at_home = Column(Boolean, default=False)
    face_routine_night = Column(Boolean, default=False)
    water_night = Column(Boolean, default=False)
    reading = Column(Boolean, default=False)
    
    # New tracked fields
    morning_routine = Column(String, default=None)
    social_media_goal = Column(Boolean, default=None)
    focus_activity = Column(String, default=None)
    movement_minutes = Column(Integer, default=None)
    night_routine = Column(String, default=None)

    # Optional text fields
    gratitude = Column(String, default=None)

    # Auto-pulled metrics (from Apple Health, MyFitnessPal, iPhone)
    calories = Column(Float, default=None)
    protein_g = Column(Float, default=None)
    steps = Column(Integer, default=None)
    sleep_hours = Column(Float, default=None)
    sleep_quality = Column(Integer, default=None)  # 1-5 scale
    screen_time_hours = Column(Float, default=None)
    temperature = Column(Float, default=None)  # Body temperature in F
    
    # Metadata
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MedicationLog(Base):
    __tablename__ = "medication_logs"
    
    id = Column(Integer, primary_key=True)
    date = Column(String, index=True)  # YYYY-MM-DD
    med_name = Column(String)  # "Med 1", "Med 2", "Med 3"
    taken = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WeeklySummary(Base):
    __tablename__ = "weekly_summaries"
    
    id = Column(Integer, primary_key=True)
    week_start = Column(String)  # YYYY-MM-DD
    summary_text = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


# Create tables
Base.metadata.create_all(bind=engine)

# =====================
# PYDANTIC MODELS
# =====================

class DailyLogSchema(BaseModel):
    date: str
    water_morning: Optional[bool] = None
    bed: Optional[bool] = None
    blinds: Optional[bool] = None
    face_routine_morning: Optional[bool] = None
    meds_taken: Optional[bool] = None
    t_break: Optional[bool] = None
    journaling: Optional[bool] = None
    eat_at_home: Optional[bool] = None
    face_routine_night: Optional[bool] = None
    water_night: Optional[bool] = None
    reading: Optional[bool] = None
    gratitude: Optional[str] = None
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    steps: Optional[int] = None
    sleep_hours: Optional[float] = None
    sleep_quality: Optional[int] = None
    screen_time_hours: Optional[float] = None
    temperature: Optional[float] = None
    morning_routine: Optional[str] = None
    social_media_goal: Optional[bool] = None
    focus_activity: Optional[str] = None
    movement_minutes: Optional[int] = None
    night_routine: Optional[str] = None

# =====================
# GOOGLE SHEETS UTILITIES
# =====================

def get_sheets_client():
    """Authenticate with Google Sheets API using service account"""
    if not GOOGLE_CREDENTIALS_JSON:
        print("⚠️ GOOGLE_CREDENTIALS_JSON not set, skipping Sheets sync")
        return None
    
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return gspread.authorize(credentials)
    except Exception as e:
        print(f"❌ Error authenticating with Google Sheets: {e}")
        return None


def sync_to_google_sheets():
    """
    Nightly sync: Get all logs from database and write to Google Sheets
    Writes all data to the first worksheet
    Sorts oldest to newest
    """
    if not GOOGLE_SHEETS_ID:
        print("⚠️ GOOGLE_SHEETS_ID not set, skipping Sheets sync")
        return
    
    client = get_sheets_client()
    if not client:
        print("❌ Failed to get sheets client")
        return
    
    db = SessionLocal()
    try:
        # Get all logs, sorted by date (oldest first)
        all_logs = db.query(DailyLog).order_by(DailyLog.date).all()
        print(f"📊 Found {len(all_logs)} logs in database")
        
        if not all_logs:
            print("📋 No logs to sync")
            return
        
        # Open the spreadsheet
        print(f"📂 Opening spreadsheet with ID: {GOOGLE_SHEETS_ID}")
        sheet = client.open_by_key(GOOGLE_SHEETS_ID)
        print(f"✅ Spreadsheet opened: '{sheet.title}'")
        
        # Get the first worksheet
        worksheet = sheet.get_worksheet(0)
        print(f"📄 Got worksheet: '{worksheet.title}'")
        
        # Clear all existing data
        print("🗑️  Clearing worksheet...")
        worksheet.clear()
        
        # Write header row
        headers = [
            "Date", "Water (AM)", "Made Bed", "Opened Blinds", 
            "Face Routine (AM)", "Meds", "T-Break", "Journaling",
            "Ate at Home", "Face Routine (PM)", "Water (PM)", "Reading",
            "Temperature (F)", "Gratitude"
        ]
        worksheet.append_row(headers)
        print(f"📋 Added header row")
        
        # Write data rows (oldest to newest)
        print(f"📝 Writing {len(all_logs)} data rows...")
        for i, log in enumerate(all_logs):
            try:
                row = [
                    log.date,
                    "✓" if log.water_morning else "✗",
                    "✓" if log.bed else "✗",
                    "✓" if log.blinds else "✗",
                    "✓" if log.face_routine_morning else "✗",
                    "✓" if log.meds_taken else "✗",
                    "✓" if log.t_break else ("✗" if log.t_break == False else "—"),
                    "✓" if log.journaling else "✗",
                    "✓" if log.eat_at_home else "✗",
                    "✓" if log.face_routine_night else "✗",
                    "✓" if log.water_night else "✗",
                    "✓" if log.reading else "✗",
                    log.temperature if log.temperature else "—",
                    log.gratitude if log.gratitude else "—",
                ]
                worksheet.append_row(row)
                if (i + 1) % 10 == 0:
                    print(f"  ✓ Appended {i + 1}/{len(all_logs)} rows")
            except Exception as row_error:
                print(f"❌ Error appending row {i} (date: {log.date}): {row_error}")
                raise
        
        print(f"✅ Google Sheets sync complete: {len(all_logs)} logs synced successfully")
    
    except Exception as e:
        print(f"❌ Error syncing to Google Sheets: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


# =====================
# TELEGRAM UTILITIES
# =====================

async def send_telegram_message(message: str):
    """Send a message to the user via Telegram bot"""
    payload = {
        "chat_id": TELEGRAM_USER_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload)


async def send_reminder(reminder_type: str):
    """Send reminder messages at scheduled times"""
    reminders = {
        "morning": "🌅 <b>Morning Check-in</b>\nDid you: water, bed, blinds, face routine, all 3 meds?\nReply: yes yes yes or describe what you did",
        "afternoon": "☀️ <b>Afternoon Check-in</b>\nDid you take a T-Break? (yes/no)",
        "evening": "🌙 <b>Evening Check-in</b>\nLogged habits: journaling, eat at home, night routine (reading, face routine, water)\nReply: yes yes yes or what you did"
    }
    await send_telegram_message(reminders.get(reminder_type, "Check-in reminder"))


def parse_telegram_message(text: str) -> dict:
    habits = {}

    # Prefix-based parsing (structured: "key: value" per line)
    prefix_map = {
        "morning":   ("morning_routine",   lambda v: v.strip().lower()),
        "meds":      ("meds_taken",        lambda v: v.strip().lower() in ("y", "yes")),
        "sleep":     ("sleep_hours",       lambda v: float(v.strip())),
        "screen":    ("social_media_goal", lambda v: v.strip().lower() in ("y", "yes")),
        "focus":     ("focus_activity",    lambda v: v.strip().upper()[0]),
        "movement":  ("movement_minutes",  lambda v: int("".join(c for c in v if c.isdigit()) or 0)),
        "night":     ("night_routine",     lambda v: v.strip().lower()),
        "temp":      ("temperature",       lambda v: float(v.strip())),
        "gratitude": ("gratitude",         lambda v: v.strip()),
    }
    for line in text.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key in prefix_map and val.strip():
                col, fn = prefix_map[key]
                try:
                    habits[col] = fn(val)
                except (ValueError, IndexError):
                    pass

    # Keyword-based fallback for free-text messages
    text_lower = text.lower()
    if any(word in text_lower for word in ["water", "drank water"]):
        habits.setdefault("water_morning", True)
    if "bed" in text_lower or "made bed" in text_lower:
        habits.setdefault("bed", True)
    if "blind" in text_lower or "opened blind" in text_lower:
        habits.setdefault("blinds", True)
    if "face routine" in text_lower or "wash face" in text_lower or "skincare" in text_lower:
        habits.setdefault("face_routine_morning", True)
    if "med" in text_lower or "took med" in text_lower or "all 3" in text_lower:
        habits.setdefault("meds_taken", True)
    if "t-break" in text_lower or "t break" in text_lower:
        habits.setdefault("t_break", "yes" in text_lower or "took" in text_lower)
    if "journal" in text_lower or "journaled" in text_lower:
        habits.setdefault("journaling", True)
    if "read" in text_lower or "reading" in text_lower:
        habits.setdefault("reading", True)
    if "eat at home" in text_lower or "ate home" in text_lower or "cooked" in text_lower:
        habits.setdefault("eat_at_home", True)
    if "night routine" in text_lower or "face routine" in text_lower or "skincare" in text_lower:
        habits.setdefault("face_routine_night", True)
    if "water" in text_lower and "night" in text_lower:
        habits.setdefault("water_night", True)

    return habits


# =====================
# FASTAPI APP
# =====================

app = FastAPI(title="Health Tracker API")
scheduler = None

@app.on_event("startup")
async def startup():
    """Initialize database and scheduler on startup"""
    global scheduler
    Base.metadata.create_all(bind=engine)
    print("✅ Database initialized")

    scheduler = AsyncIOScheduler(timezone="America/New_York")
    scheduler.add_job(sync_to_google_sheets, "cron", hour=23, minute=0)
    scheduler.add_job(morning_msg,   "cron", hour=9,  minute=0)
    scheduler.add_job(midday_msg,    "cron", hour=15, minute=0)
    scheduler.add_job(afternoon_msg, "cron", hour=17, minute=0)
    scheduler.add_job(evening_msg,   "cron", hour=20, minute=0)
    scheduler.add_job(goodnight_msg, "cron", hour=22, minute=0)
    scheduler.start()
    print("✅ Scheduler started (ET): 9am/3pm/5pm/8pm/10pm, Sheets 11pm")


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# =====================
# MANUAL SYNC ENDPOINTS
# =====================

@app.post("/sync/sheets")
async def manual_sheets_sync():
    """Manually trigger Google Sheets sync (useful for testing)"""
    sync_to_google_sheets()
    return {"status": "ok", "message": "Google Sheets sync triggered"}


@app.get("/sync/sheets/test")
async def test_sheets_sync():
    """GET endpoint to test Google Sheets sync from browser"""
    try:
        sync_to_google_sheets()
        return {"status": "ok", "message": "Google Sheets sync completed successfully"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =====================
# TELEGRAM WEBHOOK
# =====================

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Receive messages from Telegram bot
    Parses habits and stores in database
    """
    data = await request.json()
    
    if "message" not in data:
        return JSONResponse({"ok": True})
    
    message = data["message"]
    user_id = message.get("from", {}).get("id")
    text = message.get("text", "").strip()
    
    # Security: only accept from your user ID
    if str(user_id) != str(TELEGRAM_USER_ID):
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=403)

    # "hey" prefix → route to Claude (chat or reminder)
    # Return 200 immediately so Telegram doesn't retry, then process in background
    if text.lower().startswith("hey"):
        async def process_hey():
            try:
                response = await handle_hey(text, scheduler)
            except Exception as e:
                print(f"❌ handle_hey error: {e}")
                import traceback
                traceback.print_exc()
                response = f"Error: {str(e)}"
            await send_telegram_message(response)
        asyncio.create_task(process_hey())
        return JSONResponse({"ok": True})

    # Parse the message
    habits = parse_telegram_message(text)
    
    if not habits:
        await send_telegram_message("❌ I didn't catch that. Reply with habits you did, e.g.: 'water and journaled'")
        return JSONResponse({"ok": True})
    
    # Get today's date
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Update or create daily log
    db = SessionLocal()
    try:
        log = db.query(DailyLog).filter(DailyLog.date == today).first()
        if not log:
            log = DailyLog(date=today)
            db.add(log)
        
        # Update habits
        for habit, value in habits.items():
            setattr(log, habit, value)
        
        db.commit()
        
        # Confirmation message
        habit_names = ", ".join([h.replace("_", " ").title() for h in habits.keys()])
        await send_telegram_message(f"✅ Logged: {habit_names}")
        
    except Exception as e:
        db.rollback()
        print(f"Error saving habits: {e}")
        await send_telegram_message(f"❌ Error saving data: {str(e)}")
    finally:
        db.close()
    
    return JSONResponse({"ok": True})


# =====================
# DAILY LOG ENDPOINTS
# =====================

@app.get("/logs/{date}")
async def get_daily_log(date: str):
    """Get a specific day's log (format: YYYY-MM-DD)"""
    db = SessionLocal()
    log = db.query(DailyLog).filter(DailyLog.date == date).first()
    db.close()
    
    if not log:
        return JSONResponse({"error": "Log not found"}, status_code=404)
    
    return {
        "date": log.date,
        "morning": {
            "water": log.water_morning,
            "bed": log.bed,
            "blinds": log.blinds,
            "face_routine": log.face_routine_morning,
            "meds": log.meds_taken
        },
        "afternoon": {
            "t_break": log.t_break
        },
        "evening": {
            "journaling": log.journaling,
            "eat_at_home": log.eat_at_home,
            "night_routine": {
                "face_routine": log.face_routine_night,
                "water": log.water_night,
                "reading": log.reading
            }
        },
        "metrics": {
            "calories": log.calories,
            "protein_g": log.protein_g,
            "steps": log.steps,
            "sleep_hours": log.sleep_hours,
            "sleep_quality": log.sleep_quality,
            "screen_time_hours": log.screen_time_hours
        }
    }


@app.post("/logs/{date}")
async def update_daily_log(date: str, log_data: DailyLogSchema):
    """Update or create a daily log"""
    db = SessionLocal()
    try:
        log = db.query(DailyLog).filter(DailyLog.date == date).first()
        if not log:
            log = DailyLog(date=date)
            db.add(log)
        
        # Update fields if provided
        for field, value in log_data.dict(exclude_unset=True).items():
            if value is not None:
                setattr(log, field, value)
        
        db.commit()
        return {"status": "ok", "date": date}
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        db.close()


@app.get("/logs/week/{week_start}")
async def get_week_logs(week_start: str):
    """Get all logs for a week (format: YYYY-MM-DD for Monday)"""
    db = SessionLocal()
    
    start_date = datetime.strptime(week_start, "%Y-%m-%d")
    end_date = start_date + timedelta(days=7)
    
    logs = db.query(DailyLog).filter(
        DailyLog.date >= week_start,
        DailyLog.date < end_date.strftime("%Y-%m-%d")
    ).all()
    db.close()
    
    return {
        "week_start": week_start,
        "logs": [
            {
                "date": log.date,
                "habits": {
                    "morning": {
                        "water": log.water_morning,
                        "bed": log.bed,
                        "blinds": log.blinds,
                        "face_routine": log.face_routine_morning,
                        "meds": log.meds_taken
                    },
                    "afternoon": {"t_break": log.t_break},
                    "evening": {
                        "journaling": log.journaling,
                        "eat_at_home": log.eat_at_home,
                        "night_routine": {
                            "face_routine": log.face_routine_night,
                            "water": log.water_night,
                            "reading": log.reading
                        }
                    }
                },
                "metrics": {
                    "calories": log.calories,
                    "protein_g": log.protein_g,
                    "steps": log.steps,
                    "sleep_hours": log.sleep_hours,
                    "sleep_quality": log.sleep_quality,
                    "screen_time_hours": log.screen_time_hours
                }
            }
            for log in logs
        ]
    }


# =====================
# PLACEHOLDER INTEGRATIONS
# =====================

@app.post("/integrations/myfitnesspal/sync")
async def sync_myfitnesspal(date: str):
    """
    Sync MyFitnessPal data for a specific date
    TODO: Implement MFP API integration
    """
    return {"status": "ok", "message": "MFP sync placeholder", "date": date}


@app.post("/integrations/apple_health/sync")
async def sync_apple_health(date: str):
    """
    Sync Apple Health data (steps, sleep, etc.)
    Called by iOS Shortcut
    """
    return {"status": "ok", "message": "Apple Health sync placeholder", "date": date}


@app.post("/integrations/apple_health/webhook")
async def apple_health_webhook(request: Request):
    """
    Receive Apple Health data from AutoExport
    Parses AutoExport JSON format and maps metrics to database fields
    """
    data = await request.json()
    
    # Log what we received for debugging
    print(f"📥 Received AutoExport webhook:")
    print(f"Top-level keys: {list(data.keys())}")
    print(f"📄 Full JSON (first 3000 chars):")
    print(json.dumps(data, indent=2)[:3000])
    
    db = SessionLocal()
    try:
        # AutoExport sends: {"metrics": [{name: "step_count", data: [{qty, date}, ...]}, ...]}
        metrics_array = data.get("metrics", [])
        if not metrics_array or len(metrics_array) == 0:
            print(f"❌ No metrics array found")
            return JSONResponse({"error": "No metrics array in request"}, status_code=400)
        
        print(f"📊 Processing {len(metrics_array)} metrics")
        
        # Group by date and metric
        date_metrics = {}  # {date: {steps: X, calories: Y, ...}}
        
        # Map metric names that AutoExport sends (snake_case) to our database fields
        metric_map = {
            "step_count": "steps",
            "active_energy": "calories",
            "basal_energy_burned": "calories",
            "sleep_analysis": "sleep_hours",
            "screen_time": "screen_time_hours",
        }
        
        # Process each metric
        for metric in metrics_array:
            metric_name = metric.get("name", "").lower()
            metric_data = metric.get("data", [])
            
            print(f"📍 Processing metric: {metric_name} ({len(metric_data)} entries)")
            
            if not metric_data:
                continue
            
            # Get first data entry (they should all be same date with aggregation ON)
            first_entry = metric_data[0]
            date_str = first_entry.get("date", "")
            qty = first_entry.get("qty", 0)
            
            if not date_str:
                print(f"   ❌ No date in {metric_name}")
                continue
            
            # Extract YYYY-MM-DD from "2026-04-26 00:00:00 -0400" or "2026-04-26"
            if " " in date_str:
                date_str = date_str.split(" ")[0]
            
            # Initialize date entry if needed
            if date_str not in date_metrics:
                date_metrics[date_str] = {}
            
            # Map metric to our field
            if metric_name in metric_map:
                field_name = metric_map[metric_name]
                date_metrics[date_str][field_name] = qty
                print(f"   ✅ {metric_name} → {field_name}: {qty}")
        
        if not date_metrics:
            print(f"❌ No valid metrics extracted")
            return JSONResponse({"error": "No valid metrics found"}, status_code=400)
        
        # Save to database
        for date_str, metrics in date_metrics.items():
            print(f"\n💾 Saving date {date_str}: {metrics}")
            
            log = db.query(DailyLog).filter(DailyLog.date == date_str).first()
            if not log:
                log = DailyLog(date=date_str)
                db.add(log)
            
            if "steps" in metrics:
                log.steps = int(metrics["steps"])
            if "calories" in metrics:
                log.calories = float(metrics["calories"])
            if "sleep_hours" in metrics:
                log.sleep_hours = float(metrics["sleep_hours"])
            if "screen_time_hours" in metrics:
                log.screen_time_hours = float(metrics["screen_time_hours"])
            
            db.commit()
            print(f"✅ Saved {date_str}: Steps={log.steps}, Cal={log.calories}, Sleep={log.sleep_hours}h, Screen={log.screen_time_hours}h")
        
        return {"status": "ok", "dates_saved": list(date_metrics.keys())}
    
    except Exception as e:
        db.rollback()
        print(f"❌ Error processing Apple Health webhook: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        db.close()


@app.get("/debug/claude")
async def _debug_claude():
    try:
        from reminders import call_claude
        response = await call_claude("Say hello briefly.", "Hello")
        return {"ok": True, "response": response}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "type": type(e).__name__, "traceback": traceback.format_exc()}

@app.get("/debug/morning")
async def _debug_morning():
    await morning_msg()
    return {"ok": True}

@app.get("/debug/midday")
async def _debug_midday():
    await midday_msg()
    return {"ok": True}

@app.get("/debug/afternoon")
async def _debug_afternoon():
    await afternoon_msg()
    return {"ok": True}

@app.get("/debug/evening")
async def _debug_evening():
    await evening_msg()
    return {"ok": True}

@app.get("/debug/goodnight")
async def _debug_goodnight():
    await goodnight_msg()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)