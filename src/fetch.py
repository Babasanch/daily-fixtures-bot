"""
Fetch orchestration: pulls fixtures + pre-match data from API-Football for
one session (morning or evening), respecting the daily request budget, and
writes the result via storage.save_session().

Budget-aware by design -- see module-level NOTE below for the honest
tradeoff this makes when the day's fixture list is larger than the free
tier can fully cover.

Entry point (used by GitHub Actions):
    python -m src.fetch --session morning
    python -m src.fetch --session evening

NOTE ON COVERAGE:
Free-tier API-Football gives ~100 requests/day. A single call fetches every
fixture for a date across ALL leagues, which we then filter down to the
whitelist -- that part is cheap (1 call). The expensive part is per-team
statistics and per-league standings, which cost 1 call each the first time
they're needed (cached afterward, see storage.py). On a heavy fixture day,
the number of *distinct teams* needing fresh stats can exceed what's left
of the budget. When that happens, this module truncates the fixture list
(soonest kickoffs first) rather than silently dropping stats or crashing,
and marks the output as partial_coverage=True with a plain-English reason,
so the bot can be upfront with the user about it.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone as _tz
from typing import Any, Dict, List, Optional, Tuple

from . import config, storage
from .api_client import ApiFootballClient, APIFootballError, BudgetExceededError
from .rules_engine import score_all_fixtures


def _is_in_session(kickoff_wat: datetime, session: str) -> bool:
    hour = kickoff_wat.hour
    if session == "morning":
        start_h, end_h = config.MORNING_SESSION
        return start_h <= hour < end_h
    else:
        start_h, end_h = config.EVENING_SESSION
        return hour >= start_h or hour < end_h


def _build_league_lookup() -> Dict[int, Dict[str, Any]]:
    whitelist = storage.load_league_whitelist()
    if not whitelist:
        raise RuntimeError(
            "No league whitelist found. Run "
            "`python -m scripts.build_league_whitelist` first."
        )
    lookup = {}
    for entry in whitelist["domestic_leagues"] + whitelist["international_leagues"]:
        lookup[entry["id"]] = entry
    return lookup


def _fetch_raw_fixtures(client: ApiFootballClient, date_str: str) -> List[Dict[str, Any]]:
    return client.get("/fixtures", params={"date": date_str})


def _filter_and_shape_fixtures(
    raw_fixtures: List[Dict[str, Any]],
    league_lookup: Dict[int, Dict[str, Any]],
    session: str,
) -> List[Dict[str, Any]]:
    shaped = []
    for item in raw_fixtures:
        league_info = item.get("league", {})
        league_id = league_info.get("id")
        if league_id not in league_lookup:
            continue

        fixture_info = item.get("fixture", {})
        kickoff_ts = fixture_info.get("timestamp")
        if kickoff_ts is None:
            continue
        kickoff_utc = datetime.fromtimestamp(kickoff_ts, tz=_tz.utc)
        kickoff_wat = kickoff_utc.astimezone(config.WAT)

        if not _is_in_session(kickoff_wat, session):
            continue

        teams = item.get("teams", {})
        home = teams.get("home", {})
        away = teams.get("away", {})
        whitelist_entry = league_lookup[league_id]

        shaped.append({
            "fixture_id": fixture_info.get("id"),
            "league_id": league_id,
            "league_name": league_info.get("name"),
            "country": league_info.get("country"),
            "season": whitelist_entry.get("season") or league_info.get("season"),
            "kickoff_utc": kickoff_utc.isoformat(),
            "kickoff_wat": kickoff_wat.isoformat(),
            "session": session,
            "home_team": {"id": home.get("id"), "name": home.get("name")},
            "away_team": {"id": away.get("id"), "name": away.get("name")},
            "stats": {"home": {}, "away": {}},
            "standings": {"home": {}, "away": {}},
            "h2h": [],
            "injuries": {"home": None, "away": None},
        })

    shaped.sort(key=lambda f: f["kickoff_utc"])
    return shaped


def _parse_team_statistics(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Maps API-Football's /teams/statistics response into our TeamStatsBlock shape."""
    if not raw:
        return {}
    fixtures_block = raw.get("fixtures", {})
    goals_block = raw.get("goals", {})

    played = fixtures_block.get("played", {}).get("total", 0)
    form = raw.get("form", "") or ""

    gf = goals_block.get("for", {})
    ga = goals_block.get("against", {})
    gf_avg = gf.get("average", {})
    ga_avg = ga.get("average", {})

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    over_under_for = gf.get("under_over", {}) or {}

    def _over_rate(line_key):
        block = over_under_for.get(line_key)
        if not block:
            return None
        total_over = block.get("over")
        if total_over is None or not played:
            return None
        try:
            return round(int(total_over) / played, 3)
        except (TypeError, ValueError, ZeroDivisionError):
            return None

    return {
        "played": played,
        "form": form,
        "goals_for_avg": _f(gf_avg.get("total")),
        "goals_against_avg": _f(ga_avg.get("total")),
        "goals_for_avg_home": _f(gf_avg.get("home")),
        "goals_for_avg_away": _f(gf_avg.get("away")),
        "goals_against_avg_home": _f(ga_avg.get("home")),
        "goals_against_avg_away": _f(ga_avg.get("away")),
        "clean_sheets": raw.get("clean_sheet", {}).get("total", 0),
        "failed_to_score": raw.get("failed_to_score", {}).get("total", 0),
        "over_1_5_rate": _over_rate("1.5"),
        "over_2_5_rate": _over_rate("2.5"),
    }


def _parse_standings(raw: List[Any]) -> Dict[int, Dict[str, Any]]:
    """Returns {team_id: {rank, played, points, ...}} for a league's standings."""
    result = {}
    if not raw:
        return result
    groups = raw[0].get("league", {}).get("standings", []) if raw else []
    for group in groups:
        for row in group:
            team_id = row.get("team", {}).get("id")
            if team_id is None:
                continue
            result[team_id] = {
                "rank": row.get("rank"),
                "played": row.get("all", {}).get("played"),
                "points": row.get("points"),
                "goal_diff": row.get("goalsDiff"),
            }
    return result


def _parse_h2h(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for item in raw:
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        fixture_info = item.get("fixture", {})
        home_name = teams.get("home", {}).get("name")
        away_name = teams.get("away", {}).get("name")
        if home_name is None or goals.get("home") is None:
            continue
        out.append({
            "date": fixture_info.get("date"),
            "home": home_name,
            "away": away_name,
            "home_goals": goals.get("home"),
            "away_goals": goals.get("away"),
        })
    return out


def _parse_injuries(raw: List[Dict[str, Any]]) -> List[str]:
    out = []
    for item in raw:
        player = item.get("player", {})
        name = player.get("name")
        reason = player.get("reason")
        if name:
            out.append(f"{name} ({reason})" if reason else name)
    return out


def enrich_fixtures(
    client: ApiFootballClient,
    fixtures: List[Dict[str, Any]],
    max_fixtures: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    """
    Enriches the (already filtered) fixture list with team stats, standings,
    H2H, and injuries -- truncating by soonest-kickoff if the budget can't
    cover everything. Returns (enriched_fixtures, partial_coverage, note).
    """
    team_stats_cache = storage.load_team_stats_cache()
    standings_cache = storage.load_standings_cache()

    partial_coverage = False
    coverage_note = None

    working_set = fixtures
    if max_fixtures is not None and len(fixtures) > max_fixtures:
        working_set = fixtures[:max_fixtures]
        partial_coverage = True
        coverage_note = (
            f"{len(fixtures)} fixtures matched the whitelist and session "
            f"window, but only the {max_fixtures} earliest kickoffs were "
            f"processed to stay within the daily API request budget."
        )

    enriched: List[Dict[str, Any]] = []

    for fixture in working_set:
        try:
            home_id = fixture["home_team"]["id"]
            away_id = fixture["away_team"]["id"]
            league_id = fixture["league_id"]
            season = fixture["season"]

            # --- Team statistics (cached) ---
            for side, team_id in (("home", home_id), ("away", away_id)):
                cached = storage.get_cached_team_stats(team_stats_cache, team_id, league_id, season)
                if cached is not None:
                    fixture["stats"][side] = cached
                    continue
                raw = client.get("/teams/statistics", params={
                    "team": team_id, "league": league_id, "season": season,
                })
                parsed = _parse_team_statistics(raw)
                fixture["stats"][side] = parsed
                storage.set_cached_team_stats(team_stats_cache, team_id, league_id, season, parsed)

            # --- Standings (cached, per league) ---
            cached_standings = storage.get_cached_standings(standings_cache, league_id, season)
            if cached_standings is None:
                raw_standings = client.get("/standings", params={"league": league_id, "season": season})
                cached_standings = _parse_standings(raw_standings)
                storage.set_cached_standings(standings_cache, league_id, season, cached_standings)
            fixture["standings"]["home"] = cached_standings.get(home_id, {})
            fixture["standings"]["away"] = cached_standings.get(away_id, {})

            # --- Head-to-head ---
            try:
                raw_h2h = client.get("/fixtures/headtohead", params={
                    "h2h": f"{home_id}-{away_id}", "last": 5,
                })
                fixture["h2h"] = _parse_h2h(raw_h2h)
            except BudgetExceededError:
                raise
            except APIFootballError:
                fixture["h2h"] = []  # non-fatal, note stays "unavailable" downstream

            # --- Injuries ---
            try:
                raw_injuries = client.get("/injuries", params={"fixture": fixture["fixture_id"]})
                by_team: Dict[int, List[Dict[str, Any]]] = {}
                for item in raw_injuries:
                    team_id = item.get("team", {}).get("id")
                    by_team.setdefault(team_id, []).append(item)
                fixture["injuries"]["home"] = _parse_injuries(by_team.get(home_id, []))
                fixture["injuries"]["away"] = _parse_injuries(by_team.get(away_id, []))
            except BudgetExceededError:
                raise
            except APIFootballError:
                pass  # leave as None -> "unavailable", not a fabricated empty list

            enriched.append(fixture)

        except BudgetExceededError:
            partial_coverage = True
            remaining = len(working_set) - len(enriched)
            coverage_note = (
                f"{coverage_note + ' ' if coverage_note else ''}"
                f"API request budget ran out mid-session; {remaining} "
                f"fixture(s) were left without full stats and are excluded "
                f"from this session's output."
            )
            break

    storage.save_team_stats_cache(team_stats_cache)
    storage.save_standings_cache(standings_cache)

    return enriched, partial_coverage, coverage_note


def run(session: str, target_date: Optional[datetime] = None) -> None:
    if session not in ("morning", "evening"):
        raise ValueError("session must be 'morning' or 'evening'")

    target_date = target_date or datetime.now(config.WAT)
    date_str = target_date.date().isoformat()
    run_budget = config.MORNING_RUN_BUDGET if session == "morning" else config.EVENING_RUN_BUDGET

    print(f"Starting {session} fetch for {date_str} (WAT). Run budget: {run_budget}")

    client = ApiFootballClient(run_budget=run_budget)
    league_lookup = _build_league_lookup()

    try:
        raw_fixtures = _fetch_raw_fixtures(client, date_str)
    except BudgetExceededError as exc:
        print(f"ERROR: budget exhausted before any fixtures could be fetched: {exc}")
        sys.exit(1)

    print(f"Raw fixtures for {date_str}: {len(raw_fixtures)}. {client.budget_summary()}")

    shaped = _filter_and_shape_fixtures(raw_fixtures, league_lookup, session)
    print(f"Fixtures matching whitelist + {session} session window: {len(shaped)}")

    # Reserve remaining run budget for enrichment: each fixture costs up to
    # 5 calls in the worst case (2 uncached team stats + standings + h2h +
    # injuries), but standings/team-stats are usually cache-warm after the
    # first run of the day. Be conservative with the divisor.
    remaining = client.calls_remaining_this_run
    max_fixtures = max(remaining // 2, 0) if remaining else 0
    if max_fixtures == 0:
        print("WARNING: no run budget remaining for enrichment; writing fixtures without stats.")

    enriched, partial, note = enrich_fixtures(client, shaped, max_fixtures=max_fixtures or None)

    scored = score_all_fixtures(enriched)

    storage.save_session(session, {
        "target_date": date_str,
        "fixtures": scored,
        "partial_coverage": partial,
        "coverage_note": note,
    })

    print(f"Wrote {len(scored)} scored fixtures for {session} session.")
    print(client.budget_summary())
    if partial:
        print(f"NOTE (partial coverage): {note}")


def main():
    parser = argparse.ArgumentParser(description="Fetch and score fixtures for a session.")
    parser.add_argument("--session", required=True, choices=["morning", "evening"])
    parser.add_argument("--date", help="Target date YYYY-MM-DD (WAT). Defaults to today.")
    args = parser.parse_args()

    target_date = None
    if args.date:
        target_date = datetime.fromisoformat(args.date).replace(tzinfo=config.WAT)

    run(args.session, target_date)


if __name__ == "__main__":
    main()
