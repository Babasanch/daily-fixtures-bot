"""
Sanity-test the rules engine + ranking logic against synthetic fixture data
(no API calls, no network). Run with:

    python -m scripts.test_stage2
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rules_engine import score_all_fixtures
from src import rank


def make_fixture(
    fid, home, away, league="Premier League", country="England",
    home_stats=None, away_stats=None, home_rank=None, away_rank=None,
    h2h=None, injuries=None,
):
    default_stats = {
        "played": 10, "form": "WWDLW",
        "goals_for_avg": 1.5, "goals_against_avg": 1.2,
        "goals_for_avg_home": 1.7, "goals_for_avg_away": 1.3,
        "goals_against_avg_home": 1.0, "goals_against_avg_away": 1.4,
        "clean_sheets": 3, "failed_to_score": 2,
        "over_1_5_rate": 0.6, "over_2_5_rate": 0.4,
    }
    home_stats = {**default_stats, **(home_stats or {})}
    away_stats = {**default_stats, **(away_stats or {})}

    return {
        "fixture_id": fid,
        "league_id": 39,
        "league_name": league,
        "country": country,
        "kickoff_utc": "2026-07-14T14:00:00+00:00",
        "kickoff_wat": "2026-07-14T15:00:00+01:00",
        "session": "morning",
        "home_team": {"id": 1, "name": home},
        "away_team": {"id": 2, "name": away},
        "stats": {"home": home_stats, "away": away_stats},
        "standings": {
            "home": {"rank": home_rank}, "away": {"rank": away_rank},
        },
        "h2h": h2h or [],
        "injuries": injuries or {"home": None, "away": None},
    }


def main():
    fixtures = [
        # Strong home favourite, high scoring, good form, big rank gap
        make_fixture(
            1, "Man City", "Struggling FC",
            home_stats={
                "form": "WWWWW", "goals_for_avg_home": 2.6,
                "goals_against_avg_home": 0.6, "over_2_5_rate": 0.8,
                "over_1_5_rate": 0.9, "failed_to_score": 0,
            },
            away_stats={
                "form": "LLLDL", "goals_against_avg_away": 2.2,
                "goals_for_avg_away": 0.6, "failed_to_score": 6,
            },
            home_rank=1, away_rank=19,
            h2h=[
                {"date": "2025-01-01", "home": "Man City", "away": "Struggling FC",
                 "home_goals": 4, "away_goals": 0},
                {"date": "2024-05-01", "home": "Struggling FC", "away": "Man City",
                 "home_goals": 0, "away_goals": 3},
            ],
            injuries={"home": [], "away": ["Player A (injury)", "Player B (suspended)"]},
        ),
        # Even matchup, moderate everything -- shouldn't clear 80% on most markets
        make_fixture(
            2, "Mid Table United", "Mid Table City",
            home_rank=10, away_rank=11,
        ),
        # Low-scoring defensive matchup -- should score LOW on over markets
        make_fixture(
            3, "Iron Defence", "Park The Bus FC",
            home_stats={
                "form": "DDDDD", "goals_for_avg": 0.7, "goals_against_avg": 0.5,
                "clean_sheets": 7, "over_1_5_rate": 0.2, "over_2_5_rate": 0.1,
            },
            away_stats={
                "form": "DDLDD", "goals_for_avg": 0.6, "goals_against_avg": 0.6,
                "clean_sheets": 6, "over_1_5_rate": 0.15, "over_2_5_rate": 0.05,
            },
            home_rank=8, away_rank=9,
        ),
    ]

    scored = score_all_fixtures(fixtures)

    print("=" * 70)
    print("Full prediction dump per fixture")
    print("=" * 70)
    for f in scored:
        print(f"\n{f['home_team']['name']} vs {f['away_team']['name']}")
        for market, pred in f["predictions"].items():
            flag = "✅" if pred["meets_threshold"] else "  "
            print(f"  {flag} {market:12s} {pred['confidence']:5.1f}%")

    print("\n" + "=" * 70)
    print("rank.py query checks")
    print("=" * 70)

    print("\n-- home_win_picks --")
    print(json.dumps(rank.home_win_picks(scored), indent=2))

    print("\n-- over_1_5_picks --")
    print(json.dumps(rank.over_1_5_picks(scored), indent=2)[:800])

    print("\n-- banker_pick --")
    print(json.dumps(rank.banker_pick(scored), indent=2))

    print("\n-- build_accumulator(legs=5) [expect insufficient legs message] --")
    print(json.dumps(rank.build_accumulator(scored, legs=5), indent=2))

    print("\n-- build_accumulator(legs=2) --")
    print(json.dumps(rank.build_accumulator(scored, legs=2), indent=2))

    # Assertions to catch regressions automatically, not just eyeballing.
    man_city_home_win = scored[0]["predictions"]["home_win"]["confidence"]
    assert man_city_home_win > 70, f"Expected strong home win confidence, got {man_city_home_win}"

    defensive_over_2_5 = scored[2]["predictions"]["over_2_5"]["confidence"]
    assert defensive_over_2_5 < 50, f"Expected low over 2.5 confidence for defensive match, got {defensive_over_2_5}"

    print("\n✅ All sanity assertions passed.")


if __name__ == "__main__":
    main()
