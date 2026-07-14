"""
Ranking and query helpers.

Takes a list of already-scored fixtures (each with fixture["predictions"]
populated by rules_engine.score_fixture) and answers the higher-level
questions the Telegram bot commands need: "best over 1.5 picks", "safest
banker", "5-leg acca", etc.

Every function here is read-only over the fixture list -- no API calls, no
mutation -- so the bot can call these freely and instantly from the cached
daily JSON without touching the request budget.
"""
from typing import Any, Dict, List, Optional

from . import config


def _fixture_summary(fixture: Dict[str, Any], market: str) -> Dict[str, Any]:
    """Compact view of a fixture + one market's result, for display."""
    pred = fixture["predictions"][market]
    return {
        "fixture_id": fixture["fixture_id"],
        "league": fixture.get("league_name"),
        "country": fixture.get("country"),
        "kickoff_wat": fixture.get("kickoff_wat"),
        "home_team": fixture["home_team"]["name"],
        "away_team": fixture["away_team"]["name"],
        "market": market,
        "confidence": pred["confidence"],
        "reasoning": pred["reasoning"],
    }


def top_picks_for_market(
    fixtures: List[Dict[str, Any]],
    market: str,
    limit: int = 10,
    min_confidence: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Returns fixtures ranked strongest-to-weakest for a given market.
    min_confidence defaults to config.CONFIDENCE_THRESHOLD (80). Pass 0 to
    see all fixtures regardless of threshold (used internally, not exposed
    directly as a command).
    """
    threshold = config.CONFIDENCE_THRESHOLD if min_confidence is None else min_confidence
    candidates = [
        _fixture_summary(f, market)
        for f in fixtures
        if market in f.get("predictions", {}) and f["predictions"][market]["confidence"] >= threshold
    ]
    candidates.sort(key=lambda x: x["confidence"], reverse=True)
    return candidates[:limit]


def rank_all_fixtures(fixtures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ranks every fixture by its single best market confidence score,
    strongest to weakest overall. Used for general "today's ranked
    fixtures" style views.
    """
    ranked = []
    for f in fixtures:
        preds = f.get("predictions", {})
        if not preds:
            continue
        best_market, best_pred = max(preds.items(), key=lambda kv: kv[1]["confidence"])
        ranked.append({
            "fixture_id": f["fixture_id"],
            "league": f.get("league_name"),
            "country": f.get("country"),
            "kickoff_wat": f.get("kickoff_wat"),
            "home_team": f["home_team"]["name"],
            "away_team": f["away_team"]["name"],
            "best_market": best_market,
            "confidence": best_pred["confidence"],
        })
    ranked.sort(key=lambda x: x["confidence"], reverse=True)
    return ranked


# ---------------------------------------------------------------------------
# Command-specific helpers
# ---------------------------------------------------------------------------

def home_win_picks(fixtures: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    return top_picks_for_market(fixtures, "home_win", limit)


def away_win_picks(fixtures: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    return top_picks_for_market(fixtures, "away_win", limit)


def over_1_5_picks(fixtures: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    return top_picks_for_market(fixtures, "over_1_5", limit)


def over_2_5_picks(fixtures: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    return top_picks_for_market(fixtures, "over_2_5", limit)


def banker_pick(fixtures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    The single safest selection across ALL markets for the session --
    i.e. the highest confidence score found anywhere, regardless of market.
    Returns None if nothing meets the confidence threshold.
    """
    best: Optional[Dict[str, Any]] = None
    for f in fixtures:
        for market, pred in f.get("predictions", {}).items():
            if pred["confidence"] < config.CONFIDENCE_THRESHOLD:
                continue
            if best is None or pred["confidence"] > best["confidence"]:
                best = _fixture_summary(f, market)
    return best


def single_bet_of_the_day(fixtures: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Alias-with-intent over banker_pick: the one bet to headline the session.
    Kept as a separate function (rather than just reusing banker_pick
    directly in bot.py) in case the two commands diverge later -- e.g. if
    /banker should stay ultra-conservative while /single is allowed to pick
    a higher-odds, slightly lower-confidence "best value" play instead.
    """
    return banker_pick(fixtures)


def build_accumulator(
    fixtures: List[Dict[str, Any]],
    legs: int = 5,
) -> Dict[str, Any]:
    """
    Builds an N-leg accumulator from the highest-confidence, non-duplicate
    fixtures across all markets. Each leg comes from a DIFFERENT fixture
    (you can't stack two markets from the same match into one acca here --
    keeps it simple and avoids correlated-outcome legs).

    Returns a dict with the selected legs and the combined confidence
    (product of individual confidences, expressed as a percentage) so the
    compounding risk is visible rather than hidden.
    """
    # Best market per fixture, then take the strongest fixtures overall.
    candidates = rank_all_fixtures(fixtures)
    candidates = [c for c in candidates if c["confidence"] >= config.CONFIDENCE_THRESHOLD]

    selected = candidates[:legs]

    if len(selected) < legs:
        return {
            "legs": selected,
            "requested_legs": legs,
            "achieved_legs": len(selected),
            "combined_confidence": None,
            "note": (
                f"Only {len(selected)} fixture(s) met the "
                f"{config.CONFIDENCE_THRESHOLD}% confidence threshold today "
                f"-- not enough for a full {legs}-leg accumulator. Showing "
                f"what qualified instead of padding with weaker picks."
            ),
        }

    combined = 1.0
    for leg in selected:
        combined *= (leg["confidence"] / 100)
    combined_pct = round(combined * 100, 1)

    return {
        "legs": selected,
        "requested_legs": legs,
        "achieved_legs": len(selected),
        "combined_confidence": combined_pct,
        "note": (
            "Combined confidence is the product of individual leg "
            "confidences, not a guarantee -- accumulators compound risk "
            "even when each leg looks strong individually."
        ),
    }
