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
    _filter_and_shape_fixtures, _parse_raw_fixtures_to_matches, _compute_stats_from_matches,
    harvest_finished_matches, _parse_standings, _parse_h2h, _parse_injuries, _is_in_session,
)
from src import config, storage


def test_is_in_session():
    # New windows: morning 00:00-11:59 WAT, evening 12:00-23:59 WAT (no gaps, no wrap)
    morning_hour = datetime(2026, 7, 14, 5, 0, tzinfo=config.WAT)     # 05:00 -> morning
    evening_hour = datetime(2026, 7, 14, 21, 0, tzinfo=config.WAT)    # 21:00 -> evening
    midnight_hour = datetime(2026, 7, 14, 0, 0, tzinfo=config.WAT)    # 00:00 -> morning (start of day)
    eleven_hour = datetime(2026, 7, 14, 11, 45, tzinfo=config.WAT)    # 11:45 -> still morning
    noon_hour = datetime(2026, 7, 14, 12, 0, tzinfo=config.WAT)       # 12:00 -> evening (start of evening)

    assert _is_in_session(morning_hour, "morning") is True
    assert _is_in_session(morning_hour, "evening") is False
    assert _is_in_session(evening_hour, "evening") is True
    assert _is_in_session(evening_hour, "morning") is False
    assert _is_in_session(midnight_hour, "morning") is True
    assert _is_in_session(eleven_hour, "morning") is True
    assert _is_in_session(noon_hour, "evening") is True
    assert _is_in_session(noon_hour, "morning") is False

    # every hour must belong to exactly one session -- no gaps, no overlap
    for h in range(24):
        dt = datetime(2026, 7, 14, h, 0, tzinfo=config.WAT)
        m, e = _is_in_session(dt, "morning"), _is_in_session(dt, "evening")
        assert m != e, f"hour {h} matched both or neither: morning={m} evening={e}"

    print("✅ _is_in_session: new 00:00-11:59 / 12:00-23:59 WAT split covers all 24 hours, no gaps/overlap")


def test_is_in_session_wraparound_still_supported():
    """
    Confirms the generic hour-range logic still correctly handles an
    overnight wrapping window (start_h > end_h), in case a future config
    change reintroduces one -- even though the current config doesn't use
    this shape for either session anymore.
    """
    from src import config as _cfg
    original_evening = _cfg.EVENING_SESSION
    try:
        _cfg.EVENING_SESSION = (19, 4)  # old-style overnight window, for this test only
        late_night = datetime(2026, 7, 15, 2, 0, tzinfo=_cfg.WAT)   # 02:00
        evening_start = datetime(2026, 7, 14, 20, 0, tzinfo=_cfg.WAT)  # 20:00
        outside = datetime(2026, 7, 14, 10, 0, tzinfo=_cfg.WAT)  # 10:00, outside 19-4
        assert _is_in_session(late_night, "evening") is True
        assert _is_in_session(evening_start, "evening") is True
        assert _is_in_session(outside, "evening") is False
        print("✅ _is_in_session: overnight wraparound logic still works generically if reconfigured")
    finally:
        _cfg.EVENING_SESSION = original_evening  # restore real config for other tests


def test_filter_and_shape_fixtures():
    # Representative /fixtures response shape (trimmed to relevant fields).
    raw_fixtures = [
        {
            "fixture": {"id": 111, "timestamp": int(datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc).timestamp())},  # 05:00 WAT -> morning
            "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2026},
            "teams": {"home": {"id": 50, "name": "Man City"}, "away": {"id": 42, "name": "Arsenal"}},
        },
        {
            "fixture": {"id": 222, "timestamp": int(datetime(2026, 7, 14, 19, 0, tzinfo=timezone.utc).timestamp())},  # 20:00 WAT -> evening
            "league": {"id": 39, "name": "Premier League", "country": "England", "season": 2026},
            "teams": {"home": {"id": 33, "name": "Man United"}, "away": {"id": 40, "name": "Liverpool"}},
        },
        {
            "fixture": {"id": 333, "timestamp": int(datetime(2026, 7, 14, 4, 0, tzinfo=timezone.utc).timestamp())},
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


def test_parse_raw_fixtures_and_compute_stats():
    TEAM_ID = 50
    # Representative /fixtures response shape (trimmed), mixing home and
    # away appearances plus one unfinished match that should be excluded.
    raw = [
        {  # win at home, 3-1
            "fixture": {"date": "2026-07-01T15:00:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"id": TEAM_ID}, "away": {"id": 99}},
            "goals": {"home": 3, "away": 1},
        },
        {  # loss away, 0-2
            "fixture": {"date": "2026-07-04T15:00:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"id": 77}, "away": {"id": TEAM_ID}},
            "goals": {"home": 2, "away": 0},
        },
        {  # draw at home, 1-1
            "fixture": {"date": "2026-07-08T15:00:00+00:00", "status": {"short": "FT"}},
            "teams": {"home": {"id": TEAM_ID}, "away": {"id": 88}},
            "goals": {"home": 1, "away": 1},
        },
        {  # not yet played -- must be excluded
            "fixture": {"date": "2026-07-15T15:00:00+00:00", "status": {"short": "NS"}},
            "teams": {"home": {"id": TEAM_ID}, "away": {"id": 66}},
            "goals": {"home": None, "away": None},
        },
    ]

    matches = _parse_raw_fixtures_to_matches(raw, TEAM_ID)
    stats = _compute_stats_from_matches(matches)

    assert stats["played"] == 3, f"Expected 3 finished matches counted, got {stats['played']}"
    assert stats["form"] == "WLD", f"Expected form 'WLD' in chronological order, got {stats['form']}"
    assert stats["goals_for_avg"] == round((3 + 0 + 1) / 3, 2)
    assert stats["goals_against_avg"] == round((1 + 2 + 1) / 3, 2)
    assert stats["goals_for_avg_home"] == round((3 + 1) / 2, 2), "Home matches: the 3-1 win and 1-1 draw"
    assert stats["goals_for_avg_away"] == 0.0, "Away matches: only the 0-2 loss"
    assert stats["clean_sheets"] == 0, "No match had 0 goals against"
    assert stats["failed_to_score"] == 1, "One match (the 0-2 away loss) had 0 goals for"
    assert stats["over_1_5_rate"] == 1.0, "All 3 matches had 2+ total goals (4, 2, 2)"
    assert stats["over_2_5_rate"] == round(1 / 3, 3), "Only 1 of 3 matches (the 3-1) had 3+ total goals"
    print("✅ _parse_raw_fixtures_to_matches + _compute_stats_from_matches: correctly derives form/goals")

    # Unfinished-only fixture list should not crash, just return empty.
    assert _compute_stats_from_matches(_parse_raw_fixtures_to_matches([raw[3]], TEAM_ID)) == {}
    print("✅ _compute_stats_from_matches: handles no-finished-matches case gracefully")


def test_local_match_history_roundtrip():
    """
    Confirms the new local-history mechanism (storage.record_finished_match
    + get_team_matches) produces the same correct stats as the old
    API-parsing path did -- proving the switch to local history didn't
    change the actual math, just where the data comes from.
    """
    history = {}
    TEAM_ID, OPPONENT_A, OPPONENT_B = 50, 99, 77
    storage.record_finished_match(history, fixture_id=1, league_id=39, home_id=TEAM_ID, away_id=OPPONENT_A, home_goals=3, away_goals=1, date="2026-07-01T15:00:00+00:00")
    storage.record_finished_match(history, fixture_id=2, league_id=39, home_id=OPPONENT_B, away_id=TEAM_ID, home_goals=2, away_goals=0, date="2026-07-04T15:00:00+00:00")

    matches = storage.get_team_matches(history, TEAM_ID, max_matches=10)
    stats = _compute_stats_from_matches(matches)

    assert stats["played"] == 2
    assert stats["form"] == "WL", f"Expected 'WL' chronologically, got {stats['form']}"
    print("✅ storage.record_finished_match + get_team_matches: local history round-trips correctly into stats")

    # Re-recording the same fixture_id (e.g. morning + evening both harvest
    # the same 'yesterday') must not create a duplicate entry.
    storage.record_finished_match(history, fixture_id=1, league_id=39, home_id=TEAM_ID, away_id=OPPONENT_A, home_goals=3, away_goals=1, date="2026-07-01T15:00:00+00:00")
    assert len(history) == 2, "Re-harvesting the same fixture_id should overwrite, not duplicate"
    print("✅ record_finished_match: idempotent on repeated harvests of the same fixture")


def test_harvest_finished_matches():
    """Validates harvest_finished_matches correctly filters to whitelisted, finished matches only."""
    league_lookup = {39: {"id": 39, "name": "Premier League"}}
    raw = [
        {  # finished, whitelisted -- should be harvested
            "league": {"id": 39}, "fixture": {"id": 1001, "status": {"short": "FT"}, "date": "2026-07-14T15:00:00+00:00"},
            "teams": {"home": {"id": 50}, "away": {"id": 60}}, "goals": {"home": 2, "away": 1},
        },
        {  # finished but NOT whitelisted -- must be excluded
            "league": {"id": 999999}, "fixture": {"id": 1002, "status": {"short": "FT"}, "date": "2026-07-14T15:00:00+00:00"},
            "teams": {"home": {"id": 70}, "away": {"id": 80}}, "goals": {"home": 1, "away": 1},
        },
        {  # whitelisted but NOT finished -- must be excluded
            "league": {"id": 39}, "fixture": {"id": 1003, "status": {"short": "NS"}, "date": "2026-07-14T15:00:00+00:00"},
            "teams": {"home": {"id": 90}, "away": {"id": 91}}, "goals": {"home": None, "away": None},
        },
    ]

    class MockClient:
        def get(self, endpoint, params=None):
            return raw

    results = harvest_finished_matches(MockClient(), league_lookup, "2026-07-14")
    assert len(results) == 1, f"Expected exactly 1 harvested match, got {len(results)}"
    assert results[0]["fixture_id"] == 1001
    print("✅ harvest_finished_matches: correctly filters to whitelisted + finished matches only")



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
    test_is_in_session_wraparound_still_supported()
    test_filter_and_shape_fixtures()
    test_parse_raw_fixtures_and_compute_stats()
    test_local_match_history_roundtrip()
    test_harvest_finished_matches()
    test_parse_standings()
    test_parse_h2h()
    test_parse_injuries()
    print("\n✅ All Stage 3 parsing tests passed.")
