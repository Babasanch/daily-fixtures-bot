"""
Simple JSON-file storage helpers, shared by fetch.py (writer) and bot.py
(reader). Everything here is deliberately dumb -- read file, write file --
because the "database" for this project is just git-committed JSON, by
design (see project decision: keep it simple).
"""
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

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


# ---------------------------------------------------------------------------
# Match history (our own accumulated database of finished results)
# ---------------------------------------------------------------------------
# API-Football's free tier blocks every team-scoped history query we've
# tried (the `last` parameter, and `team`+`from`/`to` without a `season`,
# and `season` itself for the current year). So instead of asking the API
# for "team X's recent matches", each fetch run harvests yesterday's
# finished scores from the plain /fixtures?date=X call (which works fine,
# same as the main daily pull) and accumulates them here. Team form is then
# computed from this local archive -- zero extra API calls needed per team.

MATCH_HISTORY_FILE = os.path.join(config.DATA_DIR, "match_history.json")

# Cap how many matches we keep per team to bound file size -- far more than
# the ~10 we actually use for form, so trimming here is generous headroom.
MAX_STORED_MATCHES_PER_TEAM = 40


def load_match_history() -> Dict[str, Any]:
    """Returns {fixture_id_str: {date, league_id, home_id, away_id, home_goals, away_goals}}"""
    return _read_json(MATCH_HISTORY_FILE, default={})


def save_match_history(history: Dict[str, Any]) -> None:
    _write_json(MATCH_HISTORY_FILE, history)


def record_finished_match(
    history: Dict[str, Any], fixture_id: int, league_id: int,
    home_id: int, away_id: int, home_goals: int, away_goals: int, date: str,
) -> None:
    """Idempotent -- keyed by fixture_id, so harvesting the same date twice (e.g. morning + evening run both pulling 'yesterday') just overwrites, never duplicates."""
    history[str(fixture_id)] = {
        "date": date, "league_id": league_id,
        "home_id": home_id, "away_id": away_id,
        "home_goals": home_goals, "away_goals": away_goals,
    }


def get_team_matches(history: Dict[str, Any], team_id: int, max_matches: int = 10) -> List[Dict[str, Any]]:
    """
    Returns this team's most recent finished matches, from-the-team's-
    perspective (gf/ga/is_home), sorted oldest-to-newest, capped to
    max_matches -- ready for direct use in stats derivation.
    """
    relevant = []
    for record in history.values():
        if record["home_id"] == team_id:
            relevant.append({"gf": record["home_goals"], "ga": record["away_goals"], "is_home": True, "date": record["date"]})
        elif record["away_id"] == team_id:
            relevant.append({"gf": record["away_goals"], "ga": record["home_goals"], "is_home": False, "date": record["date"]})
    relevant.sort(key=lambda m: m["date"])
    return relevant[-max_matches:]


def prune_match_history(history: Dict[str, Any], keep_days: int = 120) -> Dict[str, Any]:
    """Drops match records older than keep_days, to stop the file growing forever."""
    cutoff = (datetime.now(config.WAT) - timedelta(days=keep_days)).date().isoformat()
    return {k: v for k, v in history.items() if v.get("date", "")[:10] >= cutoff}
