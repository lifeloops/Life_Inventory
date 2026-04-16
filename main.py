import os
import json
from datetime import datetime, timedelta
from typing import Optional

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
    
    # Auto-pulled metrics (from Apple Health, MyFitnessPal, iPhone)
    calories = Column(Float, default=None)
    protein_g = Column(Float, default=None)
    steps = Column(Integer, default=None)
    distance_km = Column(Float, default=None)
    exercise_minutes = Column(Integer, default=None)
    sleep_hours = Column(Float, default=None)
    sleep_quality = Column(Integer, default=None)  # 1-5 scale
    screen_time_hours = Column(Float, default=None)
    
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
    calories: Optional[float] = None
    protein_g: Optional[float] = None
    steps: Optional[int] = None
    distance_km: Optional[float] = None
    exercise_minutes: Optional[int] = None
    sleep_hours: Optional[float] = None
    sleep_quality: Optional[int] = None
    screen_time_hours: Optional[float] = None

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
    """
    Parse loose telegram messages and extract habits
    Examples:
    - "yes water and routine, took meds" → {water_morning: True, bed: True, blinds: True, face_routine_morning: True, meds_taken: True}
    - "journaled and ate home" → {journaling: True, eat_at_home: True}
    """
    text_lower = text.lower()
    habits = {}
    
    # Morning habits
    if any(word in text_lower for word in ["water", "drank water"]):
        habits["water_morning"] = True
    if "bed" in text_lower or "made bed" in text_lower:
        habits["bed"] = True
    if "blind" in text_lower or "opened blind" in text_lower:
        habits["blinds"] = True
    if "face routine" in text_lower or "wash face" in text_lower or "skincare" in text_lower:
        habits["face_routine_morning"] = True
    if "med" in text_lower or "took med" in text_lower or "all 3" in text_lower:
        habits["meds_taken"] = True
    
    # Afternoon
    if "t-break" in text_lower or "t break" in text_lower:
        habits["t_break"] = "yes" in text_lower or "took" in text_lower
    
    # Evening habits
    if "journal" in text_lower or "journaled" in text_lower:
        habits["journaling"] = True
    if "read" in text_lower or "reading" in text_lower:
        habits["reading"] = True
    if "eat at home" in text_lower or "ate home" in text_lower or "cooked" in text_lower:
        habits["eat_at_home"] = True
    if "night routine" in text_lower or "face routine" in text_lower or "skincare" in text_lower:
        habits["face_routine_night"] = True
    if "water" in text_lower and "night" in text_lower:
        habits["water_night"] = True
    
    return habits


# =====================
# FASTAPI APP
# =====================

app = FastAPI(title="Health Tracker API")

@app.on_event("startup")
async def startup():
    """Initialize database on startup"""
    Base.metadata.create_all(bind=engine)
    print("✅ Database initialized")


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


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
            "distance_km": log.distance_km,
            "exercise_minutes": log.exercise_minutes,
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
                    "distance_km": log.distance_km,
                    "exercise_minutes": log.exercise_minutes,
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
    Receive Apple Health data from iOS Shortcut webhook
    Expects: {date, steps, sleep_hours, sleep_quality, screen_time}
    """
    data = await request.json()
    date = data.get("date")
    
    db = SessionLocal()
    try:
        log = db.query(DailyLog).filter(DailyLog.date == date).first()
        if not log:
            log = DailyLog(date=date)
            db.add(log)
        
        # Update metrics
        if "steps" in data:
            log.steps = data["steps"]
        if "sleep_hours" in data:
            log.sleep_hours = data["sleep_hours"]
        if "sleep_quality" in data:
            log.sleep_quality = data["sleep_quality"]
        if "screen_time" in data:
            log.screen_time_hours = data["screen_time"]
        
        db.commit()
        return {"status": "ok", "date": date}
    except Exception as e:
        db.rollback()
        return JSONResponse({"error": str(e)}, status_code=400)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)