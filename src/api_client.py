"""
Thin, budget-aware client for API-Football (api-sports.io).

The free tier allows ~100 requests/day. This client:
  - Tracks a persistent daily counter (data/request_budget.json) so budget
    survives across separate GitHub Actions runs on the same day.
  - Refuses to make a call once the configured budget is exhausted, raising
    BudgetExceededError instead of silently failing or burning the real cap.
  - Retries transient errors (429/5xx) with backoff, but a 429 also counts
    as "budget hit" and stops further calls immediately.
  - Caches nothing itself — callers decide what to fetch; this module only
    guards the request count.
"""
import json
import os
import time
from datetime import datetime, date
from typing import Any, Dict, Optional

import requests

from . import config


class BudgetExceededError(Exception):
    """Raised when the daily request budget would be exceeded."""


class APIFootballError(Exception):
    """Raised for non-recoverable API errors."""


def _today_str() -> str:
    return datetime.now(config.WAT).date().isoformat()


def _load_budget_state() -> Dict[str, Any]:
    path = config.REQUEST_BUDGET_FILE
    if not os.path.exists(path):
        return {"date": _today_str(), "used": 0}
    try:
        with open(path, "r") as f:
            state = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"date": _today_str(), "used": 0}

    # Reset the counter automatically if it's a new day (WAT).
    if state.get("date") != _today_str():
        return {"date": _today_str(), "used": 0}
    return state


def _save_budget_state(state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(config.REQUEST_BUDGET_FILE), exist_ok=True)
    with open(config.REQUEST_BUDGET_FILE, "w") as f:
        json.dump(state, f, indent=2)


class ApiFootballClient:
    """
    Usage:
        client = ApiFootballClient(run_budget=config.MORNING_RUN_BUDGET)
        data = client.get("/fixtures", params={"date": "2026-07-13"})
        print(client.calls_remaining)
    """

    def __init__(self, run_budget: Optional[int] = None, max_retries: int = 3):
        if not config.API_FOOTBALL_KEY:
            raise APIFootballError(
                "API_FOOTBALL_KEY is not set. Add it as an environment "
                "variable / GitHub secret before running fetch jobs."
            )
        self.run_budget = run_budget or config.DAILY_REQUEST_BUDGET
        self.max_retries = max_retries
        self._state = _load_budget_state()
        self._calls_this_run = 0
        self._session = requests.Session()
        self._session.headers.update({
            "x-apisports-key": config.API_FOOTBALL_KEY,
        })

    @property
    def calls_used_today(self) -> int:
        return self._state["used"]

    @property
    def calls_remaining(self) -> int:
        """Remaining calls against the overall daily hard cap."""
        return max(0, config.DAILY_REQUEST_BUDGET - self._state["used"])

    @property
    def calls_remaining_this_run(self) -> int:
        """Remaining calls against this run's own sub-budget."""
        return max(0, self.run_budget - self._calls_this_run)

    def _check_budget(self) -> None:
        if self._calls_this_run >= self.run_budget:
            raise BudgetExceededError(
                f"Run budget of {self.run_budget} requests exhausted "
                f"({self._calls_this_run} used this run)."
            )
        if self._state["used"] >= config.DAILY_REQUEST_BUDGET:
            raise BudgetExceededError(
                f"Daily budget of {config.DAILY_REQUEST_BUDGET} requests "
                f"exhausted ({self._state['used']} used today)."
            )

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Perform a GET request against API-Football, enforcing budget limits.
        Returns the parsed JSON 'response' payload (the list/dict under the
        API's "response" key), raising on error.
        """
        self._check_budget()

        url = f"{config.API_FOOTBALL_BASE_URL}{endpoint}"
        last_error = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.get(url, params=params or {}, timeout=20)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))
                continue

            # Count the call against budget regardless of outcome — it hit
            # the API and consumed quota.
            self._calls_this_run += 1
            self._state["used"] += 1
            _save_budget_state(self._state)

            if resp.status_code == 429:
                # Hard stop — do not retry a rate-limit response, it just
                # burns more of the (already exhausted) quota.
                raise BudgetExceededError(
                    "API-Football returned 429 (rate limited). Stopping "
                    "further requests for this run."
                )

            if resp.status_code >= 500:
                last_error = APIFootballError(f"Server error {resp.status_code}")
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code != 200:
                raise APIFootballError(
                    f"API-Football error {resp.status_code}: {resp.text[:300]}"
                )

            payload = resp.json()
            errors = payload.get("errors")
            if errors:
                # API-Football returns errors as a dict or list depending on
                # the failure type.
                raise APIFootballError(f"API-Football returned errors: {errors}")

            return payload.get("response", [])

        raise APIFootballError(
            f"Failed after {self.max_retries} attempts: {last_error}"
        )

    def budget_summary(self) -> str:
        return (
            f"Used today: {self._state['used']}/{config.DAILY_REQUEST_BUDGET} | "
            f"This run: {self._calls_this_run}/{self.run_budget}"
        )
