"""
Internal data shapes used between fetch -> rules_engine -> rank -> bot.

These are plain dicts (kept JSON-serializable on purpose, since the whole
pipeline round-trips through git-committed JSON files) but documented here
as the single reference for field names, so fetch.py and rules_engine.py
stay in sync without guessing at each other's output shape.

--- Fixture record (as stored in data/morning.json / data/evening.json) ---
{
    "fixture_id": int,
    "league_id": int,
    "league_name": str,
    "country": str,
    "kickoff_utc": str (ISO8601),
    "kickoff_wat": str (ISO8601, West Africa Time),
    "session": "morning" | "evening",
    "home_team": {"id": int, "name": str},
    "away_team": {"id": int, "name": str},
    "stats": {
        "home": TeamStatsBlock,
        "away": TeamStatsBlock,
    },
    "standings": {
        "home": {"rank": int|None, "played": int, "points": int, ...},
        "away": {"rank": int|None, "played": int, "points": int, ...},
    },
    "h2h": [ {"date": str, "home": str, "away": str,
              "home_goals": int, "away_goals": int}, ... ] | [],
    "injuries": {
        "home": ["Player Name (reason)", ...] | None,   # None = data unavailable
        "away": ["Player Name (reason)", ...] | None,
    },
    "predictions": {          # filled in by rules_engine.py
        "over_1_5": MarketResult,
        "over_2_5": MarketResult,
        "home_over": MarketResult,
        "away_over": MarketResult,
        "home_win": MarketResult,
        "away_win": MarketResult,
    },
}

--- TeamStatsBlock (derived from /teams/statistics) ---
{
    "played": int,
    "form": str,                  # e.g. "WWLDW", most recent last
    "goals_for_avg": float,       # overall, per match
    "goals_against_avg": float,
    "goals_for_avg_home": float | None,
    "goals_for_avg_away": float | None,
    "goals_against_avg_home": float | None,
    "goals_against_avg_away": float | None,
    "clean_sheets": int,
    "failed_to_score": int,
    "over_1_5_rate": float | None,   # fraction of team's matches with 2+ total goals
    "over_2_5_rate": float | None,   # fraction of team's matches with 3+ total goals
}

--- MarketResult (output of rules_engine.py) ---
{
    "market": str,
    "confidence": float,        # 0-100
    "meets_threshold": bool,    # confidence >= config.CONFIDENCE_THRESHOLD
    "reasoning": [str, ...],    # short human-readable factors, for transparency
}
"""

# This module intentionally contains no executable logic — it exists purely
# as living documentation of the shared schema. Import it where useful for
# clarity, but don't add behavior here.
