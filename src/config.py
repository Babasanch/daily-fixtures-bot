"""
Central configuration for the football prediction bot.

All timezone-sensitive logic uses West Africa Time (WAT = UTC+1, no DST).
"""
import os
from datetime import timezone, timedelta

# ---------------------------------------------------------------------------
# Timezone
# ---------------------------------------------------------------------------
WAT = timezone(timedelta(hours=1))  # West Africa Time, fixed UTC+1, no DST

# Session windows, in WAT, as (start_hour, end_hour). Evening wraps past midnight.
MORNING_SESSION = (0, 12)    # 00:00 - 11:59 WAT
EVENING_SESSION = (12, 24)   # 12:00 - 23:59 WAT

# ---------------------------------------------------------------------------
# API-Football (api-sports.io / RapidAPI)
# ---------------------------------------------------------------------------
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "")
API_FOOTBALL_HOST = os.environ.get("API_FOOTBALL_HOST", "v3.football.api-sports.io")
API_FOOTBALL_BASE_URL = f"https://{API_FOOTBALL_HOST}"

# Free tier daily budget. We deliberately leave headroom below the hard 100
# cap so a retry or a manual run doesn't push us over.
DAILY_REQUEST_BUDGET = 90

# Split of that budget across the two scheduled runs (morning fetch fetches
# fixtures for the whole day so it gets the bigger share; evening run mostly
# reuses cached standings/form and just top-ups).
MORNING_RUN_BUDGET = 60
EVENING_RUN_BUDGET = 30

# Where the rolling counter file lives (reset daily by the fetch script).
REQUEST_BUDGET_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "request_budget.json")

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

# ---------------------------------------------------------------------------
# OpenAI (Phase 2 / Phase 3 — optional, off by default)
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")  # empty string == disabled
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")  # "gpt-5-mini" or "gpt-5"
OPENAI_ENABLED = bool(OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# League whitelist
# ---------------------------------------------------------------------------
# Populated by scripts/build_league_whitelist.py into this file. We keep it
# as a separate JSON file (not hardcoded here) because league IDs need to be
# looked up once from the API and rarely change.
LEAGUE_WHITELIST_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "league_whitelist.json")

# Countries we want top-2-division coverage for. Extend this list any time —
# rerun scripts/build_league_whitelist.py after editing it.
TARGET_COUNTRIES = [
    "England", "Spain", "Germany", "Italy", "France",
    "Netherlands", "Portugal", "Belgium", "Scotland", "Turkey",
    "Greece", "Switzerland", "Austria", "Denmark", "Sweden",
    "Norway", "Poland", "Russia", "Ukraine", "Croatia",
    "Serbia", "Czech-Republic", "Romania",
    "Brazil", "Argentina", "Mexico", "USA", "Colombia",
    "Chile", "Uruguay", "Ecuador", "Paraguay",
    "Nigeria", "Ghana", "Egypt", "Morocco", "South-Africa",
    "Tunisia", "Algeria", "Senegal", "Ivory-Coast", "Cameroon",
    "Japan", "South-Korea", "China", "Saudi-Arabia", "Qatar",
    "United-Arab-Emirates", "Australia", "India",
]

# League "types"/names that should always be included regardless of country
# filtering — internationals and friendlies.
ALWAYS_INCLUDE_LEAGUE_NAME_KEYWORDS = [
    "world cup", "euro championship", "africa cup of nations",
    "copa america", "nations league", "international friendlies",
    "friendlies", "club friendlies", "afc champions", "caf champions",
    "conmebol", "concacaf", "qualification",
]

# ---------------------------------------------------------------------------
# Betting markets & confidence thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 80  # % — minimum to surface a pick for strict markets

MARKETS = [
    "over_1_5",
    "over_2_5",
    "home_over",   # home team over their own scored-goals market
    "away_over",   # away team over their own scored-goals market
    "home_win",
    "away_win",
]

# ---------------------------------------------------------------------------
# Data cache locations (git-committed JSON, read by both the fetch job and
# the bot).
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MORNING_CACHE_FILE = os.path.join(DATA_DIR, "morning.json")
EVENING_CACHE_FILE = os.path.join(DATA_DIR, "evening.json")
