"""
Validates enrich_fixtures()'s budget-truncation behavior using a mock
client that simulates running out of API budget partway through -- no real
network calls, no real API key needed.

Team stats now come from a local match_history dict (no API call at all),
so these tests build that dict directly rather than mocking a team-scoped
API response.

Run with: python -m scripts.test_stage3_budget
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.api_client import BudgetExceededError
from src import config


class MockClient:
    """Simulates ApiFootballClient, exhausting budget after N calls. No longer needs to mock team-scoped responses, since team stats now come from local match_history instead of any API call."""
    def __init__(self, budget):
        self.budget = budget
        self.calls_made = 0

    def get(self, endpoint, params=None):
        if self.calls_made >= self.budget:
            raise BudgetExceededError("mock budget exhausted")
        self.calls_made += 1
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


def make_match_history_for_team(history, team_id):
    """Seeds 3 finished matches for a team directly into a match_history dict."""
    from src import storage
    storage.record_finished_match(history, fixture_id=team_id * 100 + 1, league_id=39,
                                   home_id=team_id, away_id=team_id + 5000,
                                   home_goals=2, away_goals=1, date="2026-07-01T15:00:00+00:00")
    storage.record_finished_match(history, fixture_id=team_id * 100 + 2, league_id=39,
                                   home_id=team_id + 5001, away_id=team_id,
                                   home_goals=0, away_goals=1, date="2026-07-05T15:00:00+00:00")
    storage.record_finished_match(history, fixture_id=team_id * 100 + 3, league_id=39,
                                   home_id=team_id, away_id=team_id + 5002,
                                   home_goals=1, away_goals=1, date="2026-07-09T15:00:00+00:00")


def test_full_budget_covers_all():
    from src.fetch import enrich_fixtures

    with tempfile.TemporaryDirectory() as tmpdir:
        config.DATA_DIR = tmpdir
        import src.storage as storage
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        fixtures = [make_fixture(i, i * 10, i * 10 + 1) for i in range(3)]

        history = {}
        for f in fixtures:
            make_match_history_for_team(history, f["home_team"]["id"])
            make_match_history_for_team(history, f["away_team"]["id"])

        client = MockClient(budget=100)
        enriched, partial, note = enrich_fixtures(client, fixtures, max_fixtures=None, match_history=history)

        assert len(enriched) == 3, f"Expected all 3 fixtures enriched, got {len(enriched)}"
        assert partial is False
        assert note is None
        assert enriched[0]["stats"]["home"]["form"] == "WWD", (
            f"Expected form 'WWD' from seeded history, got {enriched[0]['stats']['home'].get('form')}"
        )
        print("✅ enrich_fixtures: full budget processes all fixtures, stats populated from local history")


def test_truncation_via_max_fixtures():
    from src.fetch import enrich_fixtures

    with tempfile.TemporaryDirectory() as tmpdir:
        config.DATA_DIR = tmpdir
        import src.storage as storage
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        fixtures = [make_fixture(i, i * 10, i * 10 + 1) for i in range(10)]
        client = MockClient(budget=1000)
        enriched, partial, note = enrich_fixtures(client, fixtures, max_fixtures=4, match_history={})

        assert len(enriched) == 4, f"Expected 4 fixtures (truncated), got {len(enriched)}"
        assert partial is True
        assert "10 fixtures" in note and "4 earliest" in note
        print("✅ enrich_fixtures: proactive max_fixtures truncation works and is flagged honestly")


def test_mid_run_budget_exhaustion():
    from src.fetch import enrich_fixtures

    with tempfile.TemporaryDirectory() as tmpdir:
        config.DATA_DIR = tmpdir
        import src.storage as storage
        storage.STANDINGS_CACHE_FILE = os.path.join(tmpdir, "standings_cache.json")

        fixtures = [make_fixture(i, i * 10, i * 10 + 1) for i in range(5)]
        # Team stats now cost 0 API calls (local history). Remaining cost per
        # fixture is standings (cached after first, all fixtures share
        # league_id=39) + h2h(1) + injuries(1) -- so budget=6 should still
        # exhaust partway through 5 fixtures (1 standings + 5*2 = 11 calls
        # needed in total).
        client = MockClient(budget=6)
        enriched, partial, note = enrich_fixtures(client, fixtures, max_fixtures=None, match_history={})

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
