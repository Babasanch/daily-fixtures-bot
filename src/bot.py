"""
Telegram webhook bot. A lightweight Flask app that:
  - Receives Telegram updates via webhook (POST /webhook/<secret>)
  - Reads the pre-computed daily session JSON (written by fetch.py /
    GitHub Actions) -- never calls API-Football itself
  - Formats and replies to each /command

Run locally for testing:
    python -m src.bot
(defaults to Flask's dev server on port 5000; Render will run this via
gunicorn in production -- see render.yaml)

Registering the webhook with Telegram (run once after deploying):
    curl -F "url=https://<your-render-app>.onrender.com/webhook/<TELEGRAM_WEBHOOK_SECRET>" \\
         https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from flask import Flask, request, jsonify

from . import config, storage, rank

app = Flask(__name__)

TELEGRAM_API_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


# ---------------------------------------------------------------------------
# Telegram send helper
# ---------------------------------------------------------------------------

def send_message(chat_id: int, text: str) -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        print("WARNING: TELEGRAM_BOT_TOKEN not set, cannot send message.")
        return
    try:
        resp = requests.post(
            f"{TELEGRAM_API_BASE}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            print(f"Telegram sendMessage failed: {resp.status_code} {resp.text[:300]}")
    except requests.RequestException as exc:
        print(f"Telegram sendMessage error: {exc}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

MARKET_LABELS = {
    "over_1_5": "Over 1.5 Goals",
    "over_2_5": "Over 2.5 Goals",
    "home_over": "Home Team Over",
    "away_over": "Away Team Over",
    "home_win": "Home Win",
    "away_win": "Away Win",
}


def _kickoff_display(kickoff_wat_iso: Optional[str]) -> str:
    if not kickoff_wat_iso:
        return "TBD"
    try:
        dt = datetime.fromisoformat(kickoff_wat_iso)
        return dt.strftime("%H:%M WAT")
    except ValueError:
        return kickoff_wat_iso


def _format_pick_line(pick: Dict[str, Any], show_market: bool = False) -> str:
    market_str = f" — *{MARKET_LABELS.get(pick['market'], pick['market'])}*" if show_market else ""
    return (
        f"⚽ *{pick['home_team']}* vs *{pick['away_team']}*{market_str}\n"
        f"   {pick.get('league', '')} ({pick.get('country', '')}) · {_kickoff_display(pick.get('kickoff_wat'))}\n"
        f"   Confidence: *{pick['confidence']}%*"
    )


def _format_pick_list(picks: List[Dict[str, Any]], title: str, show_market: bool = False) -> str:
    if not picks:
        return (
            f"*{title}*\n\n"
            "No fixtures have been processed for this market yet. Check "
            "back after the next scheduled update, or try a different "
            "session."
        )
    lines = [f"*{title}*\n"]
    for i, pick in enumerate(picks, 1):
        lines.append(f"{i}. {_format_pick_line(pick, show_market)}")
    return "\n\n".join(lines)


def _load_combined_fixtures(session_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Loads fixtures from morning + evening caches (or just one session if
    session_filter is given). Used by commands that operate "for the day".
    """
    fixtures = []
    sessions = [session_filter] if session_filter else ["morning", "evening"]
    for s in sessions:
        data = storage.load_session(s)
        fixtures.extend(data.get("fixtures", []))
    return fixtures


def _coverage_warning(session_filter: Optional[str] = None) -> str:
    sessions = [session_filter] if session_filter else ["morning", "evening"]
    notes = []
    for s in sessions:
        data = storage.load_session(s)
        if data.get("partial_coverage") and data.get("coverage_note"):
            notes.append(f"⚠️ _{s.title()} session: {data['coverage_note']}_")
    return ("\n\n" + "\n".join(notes)) if notes else ""


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_start(chat_id: int, args: List[str]) -> None:
    text = (
        "*Welcome to your Daily Football Predictions Bot* ⚽\n\n"
        "Available commands:\n"
        "/morning — All morning session fixtures, ranked\n"
        "/evening — All evening session fixtures, ranked\n"
        "/homewin — Best home win picks\n"
        "/awaywin — Best away win picks\n"
        "/over15 — Best Over 1.5 goals picks\n"
        "/over25 — Best Over 2.5 goals picks\n"
        "/banker — Safest selection (morning & evening)\n"
        "/acca5 — Build a 5-leg accumulator\n"
        "/single — Best single bet of the day (morning & evening)\n\n"
        "Every command shows the bot's best-ranked pick(s), based on its "
        "confidence model — always the strongest available, not filtered "
        "by a fixed cutoff. Confidence is a transparent heuristic score, "
        "not a guarantee. /morning and /evening show everything processed "
        "that session. Always bet responsibly."
    )
    send_message(chat_id, text)


def _format_fixture_browse_list(fixtures: List[Dict[str, Any]], title: str, limit: int = 20) -> str:
    """
    Shows every fixture processed for a session, ranked by best market
    score. Unlike the other commands (which each show a small number of
    top picks), this lists everything the bot had data for -- useful for
    seeing the full picture behind a given day's picks.
    """
    ranked = rank.rank_all_fixtures(fixtures)
    if not ranked:
        return (
            f"*{title}*\n\n"
            "No fixtures were processed for this session yet. Check back "
            "after the next scheduled fetch, or trigger it manually from "
            "the Actions tab."
        )
    lines = [f"*{title}*\n_Showing {min(limit, len(ranked))} of {len(ranked)} fixtures, ranked by best market score_\n"]
    for i, f in enumerate(ranked[:limit], 1):
        market_label = MARKET_LABELS.get(f["best_market"], f["best_market"])
        lines.append(
            f"{i}. *{f['home_team']}* vs *{f['away_team']}*\n"
            f"   {f.get('league', '')} ({f.get('country', '')}) · {_kickoff_display(f.get('kickoff_wat'))}\n"
            f"   Best: {market_label} — *{f['confidence']}%*"
        )
    return "\n\n".join(lines)


def cmd_morning(chat_id: int, args: List[str]) -> None:
    data = storage.load_session("morning")
    text = _format_fixture_browse_list(data.get("fixtures", []), "🌅 Morning Session Fixtures") + _coverage_warning("morning")
    send_message(chat_id, text)


def cmd_evening(chat_id: int, args: List[str]) -> None:
    data = storage.load_session("evening")
    text = _format_fixture_browse_list(data.get("fixtures", []), "🌆 Evening Session Fixtures") + _coverage_warning("evening")
    send_message(chat_id, text)


def cmd_homewin(chat_id: int, args: List[str]) -> None:
    fixtures = _load_combined_fixtures()
    picks = rank.home_win_picks(fixtures, limit=10)
    text = _format_pick_list(picks, "🏠 Best Home Win Picks") + _coverage_warning()
    send_message(chat_id, text)


def cmd_awaywin(chat_id: int, args: List[str]) -> None:
    fixtures = _load_combined_fixtures()
    picks = rank.away_win_picks(fixtures, limit=10)
    text = _format_pick_list(picks, "🚗 Best Away Win Picks") + _coverage_warning()
    send_message(chat_id, text)


def cmd_over15(chat_id: int, args: List[str]) -> None:
    fixtures = _load_combined_fixtures()
    picks = rank.over_1_5_picks(fixtures, limit=10)
    text = _format_pick_list(picks, "📈 Best Over 1.5 Goals Picks") + _coverage_warning()
    send_message(chat_id, text)


def cmd_over25(chat_id: int, args: List[str]) -> None:
    fixtures = _load_combined_fixtures()
    picks = rank.over_2_5_picks(fixtures, limit=10)
    text = _format_pick_list(picks, "📈 Best Over 2.5 Goals Picks") + _coverage_warning()
    send_message(chat_id, text)


def cmd_banker(chat_id: int, args: List[str]) -> None:
    lines = ["*🔒 Banker — Safest Selections*\n"]
    for session in ("morning", "evening"):
        fixtures = _load_combined_fixtures(session)
        pick = rank.banker_pick(fixtures)
        lines.append(f"*{session.title()} Session:*")
        if pick:
            lines.append(_format_pick_line(pick, show_market=True))
        else:
            lines.append("No fixtures processed for this session yet.")
    text = "\n\n".join(lines) + _coverage_warning()
    send_message(chat_id, text)


def cmd_single(chat_id: int, args: List[str]) -> None:
    lines = ["*⭐ Single Bet of the Day*\n"]
    for session in ("morning", "evening"):
        fixtures = _load_combined_fixtures(session)
        pick = rank.single_bet_of_the_day(fixtures)
        lines.append(f"*{session.title()} Session:*")
        if pick:
            lines.append(_format_pick_line(pick, show_market=True))
        else:
            lines.append("No fixtures processed for this session yet.")
    text = "\n\n".join(lines) + _coverage_warning()
    send_message(chat_id, text)


def cmd_acca5(chat_id: int, args: List[str]) -> None:
    fixtures = _load_combined_fixtures()
    result = rank.build_accumulator(fixtures, legs=5)

    lines = ["*🎯 5-Leg Accumulator*\n"]
    if not result["legs"]:
        lines.append(
            "No fixtures have been processed yet — can't build an "
            "accumulator right now."
        )
    else:
        for i, leg in enumerate(result["legs"], 1):
            market_label = MARKET_LABELS.get(leg["best_market"], leg["best_market"])
            lines.append(
                f"{i}. *{leg['home_team']}* vs *{leg['away_team']}* — "
                f"*{market_label}* ({leg['confidence']}%)\n"
                f"   {leg.get('league', '')} · {_kickoff_display(leg.get('kickoff_wat'))}"
            )
        if result["combined_confidence"] is not None:
            lines.append(f"\n*Combined confidence:* {result['combined_confidence']}%")
        lines.append(f"\n_{result['note']}_")

    text = "\n\n".join(lines) + _coverage_warning()
    send_message(chat_id, text)


COMMAND_HANDLERS = {
    "/start": cmd_start,
    "/morning": cmd_morning,
    "/evening": cmd_evening,
    "/homewin": cmd_homewin,
    "/awaywin": cmd_awaywin,
    "/over15": cmd_over15,
    "/over25": cmd_over25,
    "/banker": cmd_banker,
    "/single": cmd_single,
    "/acca5": cmd_acca5,
}


# ---------------------------------------------------------------------------
# Flask webhook route
# ---------------------------------------------------------------------------

@app.route("/webhook/<secret>", methods=["POST"])
def webhook(secret: str):
    if not config.TELEGRAM_WEBHOOK_SECRET or secret != config.TELEGRAM_WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "invalid secret"}), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message")
    if not message:
        return jsonify({"ok": True})  # ignore non-message updates (e.g. callback queries)

    chat_id = message.get("chat", {}).get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return jsonify({"ok": True})

    parts = text.split()
    command = parts[0].split("@")[0]  # strip "@botname" suffix if present
    args = parts[1:]

    handler = COMMAND_HANDLERS.get(command)
    if handler:
        try:
            handler(chat_id, args)
        except Exception as exc:  # noqa: BLE001 -- keep bot alive on any handler error
            print(f"Error handling {command}: {exc}")
            send_message(chat_id, "Something went wrong processing that command. Please try again shortly.")
    else:
        send_message(chat_id, "Unknown command. Send /start to see available commands.")

    return jsonify({"ok": True})


@app.route("/health", methods=["GET"])
def health():
    """Simple health check endpoint for Render / uptime checks."""
    morning = storage.load_session("morning")
    evening = storage.load_session("evening")
    return jsonify({
        "status": "ok",
        "morning_fixtures": len(morning.get("fixtures", [])),
        "morning_generated_at": morning.get("generated_at_wat"),
        "evening_fixtures": len(evening.get("fixtures", [])),
        "evening_generated_at": evening.get("generated_at_wat"),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
