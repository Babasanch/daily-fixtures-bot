"""
One-off / occasional script: backfills data/match_history.json with the
last N days of finished, whitelisted match results, so team form isn't
starting from zero on day 1 of the new local-history system.

Costs exactly N API calls (1 per day backfilled) -- default 30 days, well
within a single day's budget. Safe to re-run any time; it's idempotent
(re-harvesting a date just overwrites the same fixture_ids, no duplicates).

Usage:
    python -m scripts.backfill_match_history            # last 30 days
    python -m scripts.backfill_match_history --days 14  # custom window
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, storage
from src.api_client import ApiFootballClient, APIFootballError, BudgetExceededError
from src.fetch import _build_league_lookup, harvest_finished_matches


def main():
    parser = argparse.ArgumentParser(description="Backfill local match history from the last N days.")
    parser.add_argument("--days", type=int, default=30, help="How many days back to backfill (default 30).")
    args = parser.parse_args()

    print(f"Backfilling the last {args.days} day(s) of match results...")

    client = ApiFootballClient(run_budget=args.days + 5)  # small safety margin
    league_lookup = _build_league_lookup()
    history = storage.load_match_history()

    today = datetime.now(config.WAT).date()
    total_harvested = 0

    for offset in range(1, args.days + 1):
        date_str = (today - timedelta(days=offset)).isoformat()
        try:
            harvested = harvest_finished_matches(client, league_lookup, date_str)
        except BudgetExceededError as exc:
            print(f"Stopped early -- budget exhausted: {exc}")
            break
        except APIFootballError as exc:
            print(f"WARNING: could not fetch {date_str}: {exc}")
            continue

        for m in harvested:
            storage.record_finished_match(
                history, m["fixture_id"], m["league_id"],
                m["home_id"], m["away_id"], m["home_goals"], m["away_goals"], m["date"],
            )
        total_harvested += len(harvested)
        print(f"  {date_str}: {len(harvested)} matches harvested. {client.budget_summary()}")

    history = storage.prune_match_history(history)
    storage.save_match_history(history)

    print(f"\nDone. {total_harvested} matches harvested this run, "
          f"{len(history)} total records now archived in data/match_history.json")
    print(f"{client.budget_summary()}")


if __name__ == "__main__":
    main()
