"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from pathlib import Path

try:  # optional: load backend/.env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip()
# Tried in order if the main model is retired (404) or overloaded (429/503).
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-flash-latest,gemini-flash-lite-latest").split(",") if m.strip()]

# Optional: flight lookup (free Basic plan of AeroDataBox on RapidAPI). Without it, flights are entered by hand.
AERODATABOX_API_KEY = os.getenv("AERODATABOX_API_KEY", "").strip()

# api.weather.gov asks every client to identify itself with a contact in the User-Agent.
CONTACT = os.getenv("CONTACT_EMAIL", "travel-disruption-checker@example.com")
USER_AGENT = f"TravelDisruptionChecker/1.0 ({CONTACT})"

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

# Trips shorter than this (great-circle miles) are treated as likely drives in "auto" mode.
DRIVE_THRESHOLD_MILES = float(os.getenv("DRIVE_THRESHOLD_MILES", "300"))
