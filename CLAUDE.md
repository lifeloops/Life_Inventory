# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Personal health/habit tracking pipeline. Telegram bot is the primary UI for logging habits. Backend is a single-file FastAPI app deployed on Railway with PostgreSQL.

## Commands

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Docker:
```bash
docker build -t life-inventory .
docker run --env-file .env -p 8000:8000 life-inventory
```

Copy `.env.example` to `.env` before running locally. `DATABASE_URL` is required — the app crashes on startup without it.

Deploy by pushing to Railway (no CLI; Railway CLI is broken on Windows — use the dashboard).

Schema changes go through the Railway Postgres Query tab.

## Architecture

Everything lives in `main.py` — no modules, packages, or subfolders.

**Database models** (SQLAlchemy + PostgreSQL):
- `DailyLog` — one row per calendar day (YYYY-MM-DD string key). Holds boolean habit fields AND numeric auto-pulled metrics on the same row.
- `MedicationLog` — per-medication tracking
- `WeeklySummary` — weekly summary text

**Data flows:**

| Source | Endpoint | What it writes |
|---|---|---|
| Telegram free-text messages | `POST /telegram/webhook` | Boolean habit fields on `DailyLog` |
| Apple Health (AutoExport app) | `POST /integrations/apple_health/webhook` | Numeric metric fields on `DailyLog` |
| Each Telegram log + APScheduler (11pm nightly) | internal `sync_to_google_sheets()` | Full DB → Google Sheet (clears and rewrites every time) |

**Telegram parsing** is keyword-based NLP (`parse_telegram_message()`), not a structured prefix format. It scans for words like "water", "journal", "blind", "med", "temp: 98.6", etc. Only messages from `TELEGRAM_USER_ID` are accepted.

**Tracked habit fields** (DailyLog, boolean unless noted):
- Morning: `water_morning`, `bed`, `blinds`, `face_routine_morning`, `meds_taken`
- Afternoon: `t_break`
- Evening: `journaling`, `eat_at_home`, `face_routine_night`, `water_night`, `reading`
- Text: `gratitude`
- Auto-pulled metrics (float/int): `calories`, `protein_g`, `steps`, `sleep_hours`, `sleep_quality`, `screen_time_hours`, `temperature`

**Apple Health webhook** expects AutoExport JSON format: `{"metrics": [{name, data: [{qty, date}]}]}`. Maps `step_count → steps`, `active_energy → calories`, `sleep_analysis → sleep_hours`, `screen_time → screen_time_hours`.

**Placeholder integrations** (not implemented): `POST /integrations/myfitnesspal/sync`, `POST /integrations/apple_health/sync`.

## Environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (Railway) |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_USER_ID` | Numeric Telegram user ID — used as security allowlist |
| `GOOGLE_SHEETS_ID` | Spreadsheet ID for nightly sync |
| `GOOGLE_CREDENTIALS_JSON` | Service account JSON as a single-line string (avoid multi-line — broke gspread auth previously) |

`MFP_USERNAME` / `MFP_PASSWORD` are read but unused.

## Working preferences

- Grouped multi-step instructions, not one-step-at-a-time
- Simple and unblocked over technically correct but complex
- Avoid multi-line env vars (broke gspread auth previously)
