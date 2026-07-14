"""
Exercises the bot's command handler logic (formatting, data loading) using
synthetic session JSON written to a temp data dir -- no real Telegram
token/network needed. We monkeypatch send_message to capture output instead
of calling the real Telegram API.

Run with: python -m scripts.test_stage3_bot
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config
from src.rules_engine import score_all_fixtures


def make_fixture(fid, home, away, home_stats=None, away_stats=None, home_rank=None, away_rank=None):
    default = {
        "played": 10, "form": "WWDLW", "goals_for_avg": 1.5, "goals_against_avg": 1.2,
        "goals_for_avg_home": 1.7, "goals_for_avg_away": 1.3,
        "goals_against_avg_home": 1.0, "goals_against_avg_away": 1.4,
        "clean_sheets": 3, "failed_to_score": 2, "over_1_5_rate": 0.6, "over_2_5_rate": 0.4,
    }
    return {
        "fixture_id": fid, "league_id": 39, "league_name": "Premier League",
        "country": "England", "season": 2026,
        "kickoff_utc": "2026-07-14T14:00:00+00:00", "kickoff_wat": "2026-07-14T15:00:00+01:00",
        "session": "morning",
        "home_team": {"id": fid * 10, "name": home}, "away_team": {"id": fid * 10 + 1, "name": away},
        "stats": {"home": {**default, **(home_stats or {})}, "away": {**default, **(away_stats or {})}},
        "standings": {"home": {"rank": home_rank}, "away": {"rank": away_rank}},
        "h2h": [], "injuries": {"home": None, "away": None},
    }


def main():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Redirect config paths BEFORE importing bot/storage so they pick up the temp dir.
        config.DATA_DIR = tmpdir
        config.MORNING_CACHE_FILE = os.path.join(tmpdir, "morning.json")
        config.EVENING_CACHE_FILE = os.path.join(tmpdir, "evening.json")
        config.TELEGRAM_BOT_TOKEN = "dummy"
        config.TELEGRAM_WEBHOOK_SECRET = "testsecret"

        from src import storage
        storage.TEAM_STATS_CACHE_FILE = os.path.join(tmpdir, "team_stats_cache.json")
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        # Strong home favourite -- should clear thresholds on multiple markets.
        f1 = make_fixture(
            1, "Man City", "Struggling FC",
            home_stats={"form": "WWWWW", "goals_for_avg_home": 2.6, "goals_against_avg_home": 0.6,
                        "over_2_5_rate": 0.8, "over_1_5_rate": 0.9, "failed_to_score": 0},
            away_stats={"form": "LLLDL", "goals_against_avg_away": 2.2, "goals_for_avg_away": 0.6,
                        "failed_to_score": 6},
            home_rank=1, away_rank=19,
        )
        f2 = make_fixture(2, "Mid United", "Mid City", home_rank=10, away_rank=11)

        morning_fixtures = score_all_fixtures([f1, f2])
        storage.save_session("morning", {"target_date": "2026-07-14", "fixtures": morning_fixtures,
                                          "partial_coverage": False, "coverage_note": None})
        storage.save_session("evening", {"target_date": "2026-07-14", "fixtures": [],
                                          "partial_coverage": True,
                                          "coverage_note": "Budget ran out before evening fixtures could be enriched."})

        # Now import bot AFTER data is in place, and monkeypatch send_message.
        from src import bot

        captured = []

        def fake_send_message(chat_id, text):
            captured.append((chat_id, text))

        bot.send_message = fake_send_message

        print("=" * 70)
        print("Testing /start")
        print("=" * 70)
        bot.cmd_start(123, [])
        print(captured[-1][1])

        print("\n" + "=" * 70)
        print("Testing /homewin")
        print("=" * 70)
        bot.cmd_homewin(123, [])
        print(captured[-1][1])
        assert "Man City" in captured[-1][1]

        print("\n" + "=" * 70)
        print("Testing /over25")
        print("=" * 70)
        bot.cmd_over25(123, [])
        print(captured[-1][1])

        print("\n" + "=" * 70)
        print("Testing /banker (should show evening partial coverage warning)")
        print("=" * 70)
        bot.cmd_banker(123, [])
        print(captured[-1][1])
        assert "Budget ran out" in captured[-1][1], "Expected coverage warning to surface in /banker output"

        print("\n" + "=" * 70)
        print("Testing /acca5 (insufficient legs -- should be honest about it)")
        print("=" * 70)
        bot.cmd_acca5(123, [])
        print(captured[-1][1])
        assert "5-leg" in captured[-1][1] or "threshold" in captured[-1][1]

        print("\n" + "=" * 70)
        print("Testing unknown command via webhook logic (command dispatch)")
        print("=" * 70)
        handler = bot.COMMAND_HANDLERS.get("/notreal")
        assert handler is None
        print("✅ Unknown command correctly not in COMMAND_HANDLERS")

        print("\n✅ All Stage 3 bot handler tests passed.")


if __name__ == "__main__":
    main()
