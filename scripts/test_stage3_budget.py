"""
Validates enrich_fixtures()'s budget-truncation behavior using a mock
client that simulates running out of API budget partway through -- no real
network calls, no real API key needed.

Run with: python -m scripts.test_stage3_budget
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api_client import BudgetExceededError
from src import config


class MockClient:
    """Simulates ApiFootballClient, exhausting budget after N calls."""
    def __init__(self, budget):
        self.budget = budget
        self.calls_made = 0

    def get(self, endpoint, params=None):
        if self.calls_made >= self.budget:
            raise BudgetExceededError("mock budget exhausted")
        self.calls_made += 1
        if endpoint == "/teams/statistics":
            return {
                "form": "WWDLW", "fixtures": {"played": {"total": 10}},
                "goals": {
                    "for": {"average": {"total": "1.5", "home": "1.7", "away": "1.3"},
                            "under_over": {"1.5": {"over": "6"}, "2.5": {"over": "4"}}},
                    "against": {"average": {"total": "1.0", "home": "0.8", "away": "1.2"}},
                },
                "clean_sheet": {"total": 3}, "failed_to_score": {"total": 2},
            }
        if endpoint == "/standings":
            return [{"league": {"standings": [[]]}}]
        if endpoint == "/fixtures/headtohead":
            return []
        if endpoint == "/injuries":
            return []
        return []

    @property
    def calls_remaining_this_run(self):
        return max(0, self.budget - self.calls_made)


def make_fixture(fid, home_id, away_id):
    return {
        "fixture_id": fid, "league_id": 39, "league_name": "Premier League",
        "country": "England", "season": 2026,
        "kickoff_utc": f"2026-07-14T{10+fid}:00:00+00:00",
        "kickoff_wat": f"2026-07-14T{11+fid}:00:00+01:00", "session": "morning",
        "home_team": {"id": home_id, "name": f"Home{fid}"},
        "away_team": {"id": away_id, "name": f"Away{fid}"},
        "stats": {"home": {}, "away": {}}, "standings": {"home": {}, "away": {}},
        "h2h": [], "injuries": {"home": None, "away": None},
    }


def test_full_budget_covers_all():
    from src.fetch import enrich_fixtures

    # Use a temp data dir so this test doesn't pollute real cache files.
    with tempfile.TemporaryDirectory() as tmpdir:
        config.DATA_DIR = tmpdir
        import src.storage as storage
        storage.TEAM_STATS_CACHE_FILE = os.path.join(tmpdir, "team_stats_cache.json")
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        fixtures = [make_fixture(i, i * 10, i * 10 + 1) for i in range(3)]
        client = MockClient(budget=100)  # plenty
        enriched, partial, note = enrich_fixtures(client, fixtures, max_fixtures=None)

        assert len(enriched) == 3, f"Expected all 3 fixtures enriched, got {len(enriched)}"
        assert partial is False
        assert note is None
        assert enriched[0]["stats"]["home"]["form"] == "WWDLW"
        print("✅ enrich_fixtures: full budget processes all fixtures, stats populated correctly")


def test_truncation_via_max_fixtures():
    from src.fetch import enrich_fixtures

    with tempfile.TemporaryDirectory() as tmpdir:
        config.DATA_DIR = tmpdir
        import src.storage as storage
        storage.TEAM_STATS_CACHE_FILE = os.path.join(tmpdir, "team_stats_cache.json")
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        fixtures = [make_fixture(i, i * 10, i * 10 + 1) for i in range(10)]
        client = MockClient(budget=1000)  # budget itself is fine
        # But we artificially cap max_fixtures to simulate proactive truncation
        enriched, partial, note = enrich_fixtures(client, fixtures, max_fixtures=4)

        assert len(enriched) == 4, f"Expected 4 fixtures (truncated), got {len(enriched)}"
        assert partial is True
        assert "10 fixtures" in note and "4 earliest" in note
        print("✅ enrich_fixtures: proactive max_fixtures truncation works and is flagged honestly")


def test_mid_run_budget_exhaustion():
    from src.fetch import enrich_fixtures

    with tempfile.TemporaryDirectory() as tmpdir:
        config.DATA_DIR = tmpdir
        import src.storage as storage
        storage.TEAM_STATS_CACHE_FILE = os.path.join(tmpdir, "team_stats_cache.json")
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        fixtures = [make_fixture(i, i * 10, i * 10 + 1) for i in range(5)]
        # Each fixture costs ~4 calls (2 team stats + standings + h2h + injuries
        # = 5 actually, minus caching effects). Give enough for ~1-2 fixtures.
        client = MockClient(budget=6)
        enriched, partial, note = enrich_fixtures(client, fixtures, max_fixtures=None)

        assert len(enriched) < 5, f"Expected fewer than 5 fixtures due to budget exhaustion, got {len(enriched)}"
        assert partial is True
        assert "budget ran out" in note
        print(f"✅ enrich_fixtures: mid-run budget exhaustion handled gracefully "
              f"({len(enriched)}/5 fixtures processed, correctly flagged partial)")


if __name__ == "__main__":
    test_full_budget_covers_all()
    test_truncation_via_max_fixtures()
    test_mid_run_budget_exhaustion()
    print("\n✅ All Stage 3 budget-truncation tests passed.")
