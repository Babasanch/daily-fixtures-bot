"""
Simple JSON-file storage helpers, shared by fetch.py (writer) and bot.py
(reader). Everything here is deliberately dumb -- read file, write file --
because the "database" for this project is just git-committed JSON, by
design (see project decision: keep it simple).
"""
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from . import config


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp_path, path)  # atomic-ish swap, avoids half-written files


# ---------------------------------------------------------------------------
# Daily session output (what the bot reads to answer commands)
# ---------------------------------------------------------------------------

def load_session(session: str) -> Dict[str, Any]:
    """session: 'morning' or 'evening'"""
    path = config.MORNING_CACHE_FILE if session == "morning" else config.EVENING_CACHE_FILE
    return _read_json(path, default={
        "session": session,
        "generated_at_wat": None,
        "target_date": None,
        "fixtures": [],
        "partial_coverage": False,
        "coverage_note": None,
    })


def save_session(session: str, payload: Dict[str, Any]) -> None:
    path = config.MORNING_CACHE_FILE if session == "morning" else config.EVENING_CACHE_FILE
    payload["session"] = session
    payload["generated_at_wat"] = datetime.now(config.WAT).isoformat()
    _write_json(path, payload)


# ---------------------------------------------------------------------------
# League whitelist (built by scripts/build_league_whitelist.py)
# ---------------------------------------------------------------------------

def load_league_whitelist() -> Optional[Dict[str, Any]]:
    return _read_json(config.LEAGUE_WHITELIST_FILE, default=None)


# ---------------------------------------------------------------------------
# Team statistics cache (cross-day, TTL-based)
# ---------------------------------------------------------------------------

TEAM_STATS_CACHE_FILE = os.path.join(config.DATA_DIR, "team_stats_cache.json")
STANDINGS_CACHE_FILE = os.path.join(config.DATA_DIR, "standings_cache.json")

# Shorter TTL than before -- recent-form stats are now derived from a team's
# actual last N match results, so they go stale as soon as that team plays
# again. 2 days balances freshness against budget (a team rarely plays more
# than once every 2-3 days anyway).
TEAM_STATS_TTL_DAYS = 2
STANDINGS_TTL_DAYS = 1


def _cache_key_fresh(entry: Dict[str, Any], ttl_days: int) -> bool:
    fetched_at = entry.get("fetched_at")
    if not fetched_at:
        return False
    try:
        fetched_dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    age_days = (datetime.now(config.WAT) - fetched_dt).total_seconds() / 86400
    return age_days < ttl_days


def load_team_stats_cache() -> Dict[str, Any]:
    return _read_json(TEAM_STATS_CACHE_FILE, default={})


def save_team_stats_cache(cache: Dict[str, Any]) -> None:
    _write_json(TEAM_STATS_CACHE_FILE, cache)


def get_cached_team_stats(cache: Dict[str, Any], team_id: int) -> Optional[Dict[str, Any]]:
    """
    Keyed by team_id only -- stats are derived from that team's recent match
    results regardless of which fixture/league/season triggered the fetch,
    so the same cached entry is valid for any fixture involving this team.
    """
    key = str(team_id)
    entry = cache.get(key)
    if entry and _cache_key_fresh(entry, TEAM_STATS_TTL_DAYS):
        return entry["stats"]
    return None


def set_cached_team_stats(cache: Dict[str, Any], team_id: int, stats: Dict[str, Any]) -> None:
    key = str(team_id)
    cache[key] = {"fetched_at": datetime.now(config.WAT).isoformat(), "stats": stats}


def load_standings_cache() -> Dict[str, Any]:
    return _read_json(STANDINGS_CACHE_FILE, default={})


def save_standings_cache(cache: Dict[str, Any]) -> None:
    _write_json(STANDINGS_CACHE_FILE, cache)


def get_cached_standings(cache: Dict[str, Any], league_id: int, season: int) -> Optional[Dict[str, Any]]:
    key = f"{league_id}:{season}"
    entry = cache.get(key)
    if entry and _cache_key_fresh(entry, STANDINGS_TTL_DAYS):
        return entry["standings"]
    return None


def set_cached_standings(cache: Dict[str, Any], league_id: int, season: int, standings: Dict[str, Any]) -> None:
    key = f"{league_id}:{season}"
    cache[key] = {"fetched_at": datetime.now(config.WAT).isoformat(), "standings": standings}
