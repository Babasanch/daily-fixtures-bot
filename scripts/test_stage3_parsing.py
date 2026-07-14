"""
Validates fetch.py's response-parsing functions against realistic
API-Football response shapes (no network calls -- pure unit tests on
representative payloads matching the documented API structure).

Run with: python -m scripts.test_stage3_parsing
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.fetch import (
    _filter_and_shape_fixtures, _parse_team_statistics,
    _parse_standings, _parse_h2h, _parse_injuries, _is_in_session,
)
from src import config


def test_is_in_session():
    morning_hour = datetime(2026, 7, 14, 10, 0, tzinfo=config.WAT)
    evening_hour = datetime(2026, 7, 14, 21, 0, tzinfo=config.WAT)
    late_night_hour = datetime(2026, 7, 15, 2, 0, tzinfo=config.WAT)  # 02:00, still "evening" session
    outside_hour = datetime(2026, 7, 14, 5, 0, tzinfo=config.WAT)  # 05:00, neither session

    assert _is_in_session(morning_hour, "morning") is True
    assert _is_in_session(morning_hour, "evening") is False
    assert _is_in_session(evening_hour, "evening") is True
    assert _is_in_session(late_night_hour, "evening") is True, "02:00 should count as evening session (wraps past midnight)"
    assert _is_in_session(outside_hour, "morning") is False
    assert _is_in_session(outside_hour, "evening") is False
    print("✅ _is_in_session: session window boundaries correct, including evening wraparound")


def test_filter_and_shape_fixtures():
    # Representative /fixtures response shape (trimmed to relevant fields).
    raw_fixtures = [
        {
            "fixture": {"id": 111, "timestamp": int(datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc).timestamp())},  # 13:00 WAT -> morning
            "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2026},
            "teams": {"home": {"id": 50, "name": "Man City"}, "away": {"id": 42, "name": "Arsenal"}},
        },
        {
            "fixture": {"id": 222, "timestamp": int(datetime(2026, 7, 14, 19, 0, tzinfo=timezone.utc).timestamp())},  # 20:00 WAT -> evening
            "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2026},
            "teams": {"home": {"id": 33, "name": "Man United"}, "away": {"id": 40, "name": "Liverpool"}},
        },
        {
            "fixture": {"id": 333, "timestamp": int(datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc).timestamp())},
            "league": {"id": 999999, "name": "Some Random Cup", "country": "Nowhere", "season": 2026},  # not whitelisted
            "teams": {"home": {"id": 1, "name": "X"}, "away": {"id": 2, "name": "Y"}},
        },
    ]
    league_lookup = {39: {"id": 39, "name": "Premier League", "country": "England", "season": 2026, "tier": "1"}}

    morning_shaped = _filter_and_shape_fixtures(raw_fixtures, league_lookup, "morning")
    evening_shaped = _filter_and_shape_fixtures(raw_fixtures, league_lookup, "evening")

    assert len(morning_shaped) == 1, f"Expected 1 morning fixture, got {len(morning_shaped)}"
    assert morning_shaped[0]["fixture_id"] == 111
    assert morning_shaped[0]["home_team"]["name"] == "Man City"

    assert len(evening_shaped) == 1, f"Expected 1 evening fixture, got {len(evening_shaped)}"
    assert evening_shaped[0]["fixture_id"] == 222

    # Non-whitelisted league (999999) must never appear in either session.
    all_ids = {f["fixture_id"] for f in morning_shaped + evening_shaped}
    assert 333 not in all_ids, "Non-whitelisted league fixture leaked through the filter"

    print("✅ _filter_and_shape_fixtures: whitelist filtering + session splitting correct")


def test_parse_team_statistics():
    # Representative /teams/statistics response shape (trimmed).
    raw = {
        "form": "WWDLW",
        "fixtures": {"played": {"total": 20}},
        "goals": {
            "for": {
                "average": {"total": "1.8", "home": "2.1", "away": "1.5"},
                "under_over": {
                    "1.5": {"over": "16", "under": "4"},
                    "2.5": {"over": "9", "under": "11"},
                },
            },
            "against": {
                "average": {"total": "1.1", "home": "0.9", "away": "1.3"},
            },
        },
        "clean_sheet": {"total": 7},
        "failed_to_score": {"total": 3},
    }
    parsed = _parse_team_statistics(raw)
    assert parsed["played"] == 20
    assert parsed["form"] == "WWDLW"
    assert parsed["goals_for_avg"] == 1.8
    assert parsed["goals_for_avg_home"] == 2.1
    assert parsed["goals_against_avg_away"] == 1.3
    assert parsed["over_1_5_rate"] == 0.8, f"Expected 16/20=0.8, got {parsed['over_1_5_rate']}"
    assert parsed["over_2_5_rate"] == 0.45, f"Expected 9/20=0.45, got {parsed['over_2_5_rate']}"
    assert parsed["clean_sheets"] == 7
    assert parsed["failed_to_score"] == 3
    print("✅ _parse_team_statistics: field mapping and over-rate math correct")

    # Empty/missing response should not crash.
    assert _parse_team_statistics({}) == {}
    assert _parse_team_statistics(None) == {}
    print("✅ _parse_team_statistics: handles empty/missing data gracefully")


def test_parse_standings():
    # Representative /standings response shape (single-group league).
    raw = [
        {
            "league": {
                "standings": [
                    [
                        {"rank": 1, "team": {"id": 50, "name": "Man City"},
                         "points": 55, "goalsDiff": 40, "all": {"played": 22}},
                        {"rank": 2, "team": {"id": 42, "name": "Arsenal"},
                         "points": 50, "goalsDiff": 30, "all": {"played": 22}},
                    ]
                ]
            }
        }
    ]
    parsed = _parse_standings(raw)
    assert parsed[50]["rank"] == 1
    assert parsed[50]["points"] == 55
    assert parsed[42]["rank"] == 2
    print("✅ _parse_standings: flattening and field extraction correct")

    assert _parse_standings([]) == {}
    print("✅ _parse_standings: handles empty response gracefully")


def test_parse_h2h():
    raw = [
        {
            "fixture": {"date": "2025-03-01T15:00:00+00:00"},
            "teams": {"home": {"name": "Man City"}, "away": {"name": "Arsenal"}},
            "goals": {"home": 3, "away": 1},
        },
        {
            "fixture": {"date": "2024-10-01T15:00:00+00:00"},
            "teams": {"home": {"name": "Arsenal"}, "away": {"name": "Man City"}},
            "goals": {"home": 0, "away": 0},
        },
    ]
    parsed = _parse_h2h(raw)
    assert len(parsed) == 2
    assert parsed[0]["home"] == "Man City"
    assert parsed[0]["home_goals"] == 3
    print("✅ _parse_h2h: field extraction correct")


def test_parse_injuries():
    raw = [
        {"player": {"name": "Kevin De Bruyne", "reason": "Hamstring Injury"}},
        {"player": {"name": "John Doe", "reason": None}},
    ]
    parsed = _parse_injuries(raw)
    assert parsed[0] == "Kevin De Bruyne (Hamstring Injury)"
    assert parsed[1] == "John Doe"
    print("✅ _parse_injuries: formatting correct (with and without reason)")


if __name__ == "__main__":
    test_is_in_session()
    test_filter_and_shape_fixtures()
    test_parse_team_statistics()
    test_parse_standings()
    test_parse_h2h()
    test_parse_injuries()
    print("\n✅ All Stage 3 parsing tests passed.")
