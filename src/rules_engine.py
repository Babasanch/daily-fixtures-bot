"""
Rules engine: turns a fixture's gathered pre-match data into a confidence
score (0-100) for each betting market.

IMPORTANT - what this score is and isn't:
This is a transparent, weighted heuristic score, not a calibrated statistical
probability. It has not been backtested against historical outcomes. It is
labeled "confidence score" everywhere it's surfaced to the user, and every
result carries a `reasoning` list explaining exactly which factors drove the
number, so nothing is a black box. If you start logging actual results, this
is the module to revisit for calibration.

Scoring approach, per market:
    - Start from a baseline of 50 (coin-flip / no edge).
    - Add/subtract points for each supporting or opposing factor, capped so
      no single factor can dominate.
    - Missing data (e.g. no H2H, no injury feed for that league) simply
      contributes 0 -- it neither helps nor hurts the score, and is noted in
      the reasoning as "unavailable" rather than silently ignored.
    - Final score is clamped to [0, 100].
"""
from typing import Any, Dict, List, Optional

from . import config


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _form_score(form: Optional[str]) -> float:
    """
    Converts a form string like "WWLDW" (most recent last) into a -10..+10
    style contribution, weighting recent matches more heavily.
    """
    if not form:
        return 0.0
    weights = [1.0, 1.2, 1.4, 1.6, 2.0]  # oldest -> most recent
    letters = list(form[-5:])
    w = weights[-len(letters):]
    points = {"W": 1.0, "D": 0.0, "L": -1.0}
    total = sum(points.get(letter, 0.0) * weight for letter, weight in zip(letters, w))
    max_possible = sum(w)
    return (total / max_possible) * 10 if max_possible else 0.0


def _safe_avg(value: Optional[float]) -> float:
    return value if isinstance(value, (int, float)) else 0.0


# ---------------------------------------------------------------------------
# Over/Under goal markets
# ---------------------------------------------------------------------------

def score_over_market(fixture: Dict[str, Any], line: float) -> Dict[str, Any]:
    """
    Scores Over 1.5 or Over 2.5 total-goals markets.
    line: 1.5 or 2.5
    """
    reasoning: List[str] = []
    home = fixture["stats"]["home"]
    away = fixture["stats"]["away"]

    score = 50.0

    # Combined expected goals: home's scoring avg + away's conceding avg,
    # and vice versa, averaged -- a simple but transparent proxy.
    home_gf = _safe_avg(home.get("goals_for_avg_home") or home.get("goals_for_avg"))
    home_ga = _safe_avg(home.get("goals_against_avg_home") or home.get("goals_against_avg"))
    away_gf = _safe_avg(away.get("goals_for_avg_away") or away.get("goals_for_avg"))
    away_ga = _safe_avg(away.get("goals_against_avg_away") or away.get("goals_against_avg"))

    expected_goals = ((home_gf + away_ga) / 2) + ((away_gf + home_ga) / 2)
    reasoning.append(f"Combined expected goals estimate: {expected_goals:.2f}")

    if expected_goals > 0:
        margin = expected_goals - line
        score += _clamp(margin * 18, -30, 30)

    rate_key = "over_1_5_rate" if line == 1.5 else "over_2_5_rate"
    home_rate = home.get(rate_key)
    away_rate = away.get(rate_key)
    rates = [r for r in (home_rate, away_rate) if isinstance(r, (int, float))]
    if rates:
        avg_rate = sum(rates) / len(rates)
        score += _clamp((avg_rate - 0.5) * 40, -20, 20)
        reasoning.append(
            f"Team over-{line} history rate: {avg_rate:.0%} "
            f"(home {home_rate if home_rate is not None else 'n/a'}, "
            f"away {away_rate if away_rate is not None else 'n/a'})"
        )
    else:
        reasoning.append(f"Team over-{line} historical rate: unavailable")

    home_form = _form_score(home.get("form"))
    away_form = _form_score(away.get("form"))
    form_adj = (home_form + away_form) / 2
    score += _clamp(form_adj * 0.6, -6, 6)
    if home.get("form") or away.get("form"):
        reasoning.append(
            f"Recent form: home {home.get('form', 'n/a')}, away {away.get('form', 'n/a')}"
        )

    home_played = max(home.get("played", 0), 1)
    away_played = max(away.get("played", 0), 1)
    low_scoring_signal = (
        (home.get("clean_sheets", 0) / home_played) +
        (away.get("clean_sheets", 0) / away_played) +
        (home.get("failed_to_score", 0) / home_played) +
        (away.get("failed_to_score", 0) / away_played)
    ) / 4
    score -= _clamp(low_scoring_signal * 15, 0, 12)
    if low_scoring_signal > 0:
        reasoning.append(f"Defensive/low-scoring tendency signal: {low_scoring_signal:.0%}")

    final_score = round(_clamp(score), 1)
    return {
        "market": f"over_{str(line).replace('.', '_')}",
        "confidence": final_score,
        "meets_threshold": final_score >= config.CONFIDENCE_THRESHOLD,
        "reasoning": reasoning,
    }


def score_team_over_market(fixture: Dict[str, Any], side: str) -> Dict[str, Any]:
    """
    "H/A over goal" -- confidence that the specified side (home or away)
    scores over their own typical goal line. Uses expected-goals framing
    since API-Football's free tier doesn't expose granular per-team
    over/under split markets directly.
    side: "home" or "away"
    """
    reasoning: List[str] = []
    team = fixture["stats"][side]
    opponent = fixture["stats"]["away" if side == "home" else "home"]

    score = 50.0

    team_gf = _safe_avg(
        team.get(f"goals_for_avg_{side}") or team.get("goals_for_avg")
    )
    opp_side = "away" if side == "home" else "home"
    opp_ga = _safe_avg(
        opponent.get(f"goals_against_avg_{opp_side}") or opponent.get("goals_against_avg")
    )
    expected = (team_gf + opp_ga) / 2
    reasoning.append(f"{side.title()} team expected goals estimate: {expected:.2f}")
    score += _clamp((expected - 1.0) * 25, -25, 25)

    played = max(team.get("played", 0), 1)
    fts_rate = team.get("failed_to_score", 0) / played
    score -= _clamp(fts_rate * 40, 0, 25)
    if team.get("failed_to_score") is not None:
        reasoning.append(f"{side.title()} team failed-to-score rate: {fts_rate:.0%}")

    form_adj = _form_score(team.get("form"))
    score += _clamp(form_adj * 0.8, -8, 8)

    final_score = round(_clamp(score), 1)
    return {
        "market": f"{side}_over",
        "confidence": final_score,
        "meets_threshold": final_score >= config.CONFIDENCE_THRESHOLD,
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Win markets
# ---------------------------------------------------------------------------

def score_win_market(fixture: Dict[str, Any], side: str) -> Dict[str, Any]:
    """
    side: "home" or "away" -- confidence that this side wins the match.
    """
    reasoning: List[str] = []
    team = fixture["stats"][side]
    opponent = fixture["stats"]["away" if side == "home" else "home"]
    team_standing = fixture.get("standings", {}).get(side, {})
    opp_standing = fixture.get("standings", {}).get(
        "away" if side == "home" else "home", {}
    )

    score = 50.0

    if side == "home":
        score += 4
        reasoning.append("Home advantage applied")

    team_rank = team_standing.get("rank")
    opp_rank = opp_standing.get("rank")
    if isinstance(team_rank, int) and isinstance(opp_rank, int):
        gap = opp_rank - team_rank  # positive if team ranked better (lower number)
        score += _clamp(gap * 1.3, -22, 22)
        reasoning.append(f"League position: {side} #{team_rank} vs opponent #{opp_rank}")
    else:
        reasoning.append("League position data unavailable for one or both teams")

    team_form = _form_score(team.get("form"))
    opp_form = _form_score(opponent.get("form"))
    form_gap = team_form - opp_form
    score += _clamp(form_gap * 1.5, -18, 18)
    if team.get("form") or opponent.get("form"):
        reasoning.append(
            f"Form: {side} {team.get('form', 'n/a')} vs opponent {opponent.get('form', 'n/a')}"
        )

    team_gd = _safe_avg(team.get("goals_for_avg")) - _safe_avg(team.get("goals_against_avg"))
    opp_gd = _safe_avg(opponent.get("goals_for_avg")) - _safe_avg(opponent.get("goals_against_avg"))
    gd_gap = team_gd - opp_gd
    score += _clamp(gd_gap * 8, -16, 16)
    reasoning.append(f"Goal-difference-per-game gap: {gd_gap:+.2f}")

    h2h = fixture.get("h2h") or []
    if h2h:
        team_name = fixture[f"{side}_team"]["name"]
        wins = 0
        for match in h2h:
            winner = None
            if match["home_goals"] > match["away_goals"]:
                winner = match["home"]
            elif match["away_goals"] > match["home_goals"]:
                winner = match["away"]
            if winner == team_name:
                wins += 1
        win_rate = wins / len(h2h)
        score += _clamp((win_rate - 0.5) * 20, -10, 10)
        reasoning.append(f"Head-to-head: {side} won {wins}/{len(h2h)} recent meetings")
    else:
        reasoning.append("Head-to-head history unavailable")

    injuries = fixture.get("injuries", {}) or {}
    team_injuries = injuries.get(side)
    opp_injuries = injuries.get("away" if side == "home" else "home")
    if team_injuries is not None or opp_injuries is not None:
        team_count = len(team_injuries or [])
        opp_count = len(opp_injuries or [])
        score += _clamp((opp_count - team_count) * 2, -8, 8)
        reasoning.append(
            f"Injuries/suspensions: {side} {team_count} out, opponent {opp_count} out"
        )
    else:
        reasoning.append("Injury/suspension data unavailable")

    final_score = round(_clamp(score), 1)
    return {
        "market": f"{side}_win",
        "confidence": final_score,
        "meets_threshold": final_score >= config.CONFIDENCE_THRESHOLD,
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def score_fixture(fixture: Dict[str, Any]) -> Dict[str, Any]:
    """
    Runs every market scorer against a single fixture and attaches the
    results under fixture["predictions"]. Returns the mutated fixture for
    convenience (also mutates in place).
    """
    fixture["predictions"] = {
        "over_1_5": score_over_market(fixture, 1.5),
        "over_2_5": score_over_market(fixture, 2.5),
        "home_over": score_team_over_market(fixture, "home"),
        "away_over": score_team_over_market(fixture, "away"),
        "home_win": score_win_market(fixture, "home"),
        "away_win": score_win_market(fixture, "away"),
    }
    return fixture


def score_all_fixtures(fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [score_fixture(f) for f in fixtures]
