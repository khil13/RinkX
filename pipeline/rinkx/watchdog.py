"""Watchdog (Phase 10): notice when the pipeline itself stops working.

Runs from its own workflow (watchdog.yml), so it still runs when pipeline.yml is failing. If
there has been no successful pipeline run for 2 hours on a game day (26 hours on other days), it:
  * opens one GitHub issue (GitHub emails the owner), and
  * pushes one ntfy notification when NTFY_TOPIC is set.
When a run succeeds again, it comments and closes the issue. It never repeats an alert while the
issue is open. Both messages carry only timestamps and a link to the Actions page: the repository
is public.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from rinkx.config import Settings
from rinkx.timeutil import parse_iso, slate_date, utcnow

log = logging.getLogger("rinkx.watchdog")

GAME_DAY_LIMIT = timedelta(hours=2)
OTHER_DAY_LIMIT = timedelta(hours=26)
ISSUE_TITLE = "Watchdog: the pipeline has stopped succeeding"
SCHEDULE_URL = "https://api-web.nhle.com/v1/schedule/{day}"


class Repo(Protocol):
    def last_success(self) -> datetime | None: ...
    def open_issue(self) -> int | None: ...
    def create_issue(self, title: str, body: str) -> None: ...
    def comment(self, number: int, body: str) -> None: ...
    def close(self, number: int) -> None: ...


class GhRepo:
    """The repository through the `gh` CLI (GH_TOKEN in Actions)."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def _gh(self, *args: str) -> str:
        proc = subprocess.run(["gh", *args, "--repo", self.repo], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"gh {args[0]} {args[1]} failed: {proc.stderr.strip()}")
        return proc.stdout

    def last_success(self) -> datetime | None:
        out = self._gh(
            "run", "list", "--workflow", "pipeline.yml", "--status", "success", "--limit", "1", "--json", "updatedAt"
        )
        runs = json.loads(out)
        return parse_iso(runs[0]["updatedAt"]) if runs else None

    def open_issue(self) -> int | None:
        out = self._gh(
            "issue", "list", "--state", "open", "--search", f'"{ISSUE_TITLE}" in:title', "--json", "number,title"
        )
        hits = [i["number"] for i in json.loads(out) if i["title"] == ISSUE_TITLE]
        return int(hits[0]) if hits else None

    def create_issue(self, title: str, body: str) -> None:
        self._gh("issue", "create", "--title", title, "--body", body)

    def comment(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--body", body)

    def close(self, number: int) -> None:
        self._gh("issue", "close", str(number), "--reason", "completed")


def nhl_game_day(day: date, fetch: Callable[[str], Any] | None = None) -> bool | None:
    """True/False from the NHL schedule; None if it couldn't be read."""

    def _get(url: str) -> Any:
        with urllib.request.urlopen(url, timeout=20) as resp:
            return json.loads(resp.read())

    try:
        data = (fetch or _get)(SCHEDULE_URL.format(day=day.isoformat()))
        for d in data.get("gameWeek", []):
            if d.get("date") == day.isoformat():
                return bool(d.get("numberOfGames") or d.get("games"))
        return False
    except Exception as exc:
        log.warning("NHL schedule unavailable (%s); using the game-day limit", type(exc).__name__)
        return None


@dataclass(frozen=True)
class Verdict:
    stale: bool
    age: timedelta | None
    limit: timedelta


def check(now: datetime, last: datetime | None, game_day: bool | None) -> Verdict:
    limit = OTHER_DAY_LIMIT if game_day is False else GAME_DAY_LIMIT  # unknown: be strict
    age = now - last if last else None
    return Verdict(age is None or age > limit, age, limit)


def _hours(td: timedelta) -> str:
    return f"{td.total_seconds() / 3600:.1f} h"


def watch(
    repo: Repo,
    now: datetime,
    game_day: bool | None,
    actions_url: str,
    send: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Returns 'ok', 'alerted', 'still_stale' or 'recovered'."""
    last = repo.last_success()
    v = check(now, last, game_day)
    issue = repo.open_issue()
    since = last.strftime("%Y-%m-%d %H:%M UTC") if last else "never"
    if v.stale:
        if issue is not None:
            return "still_stale"
        body = (
            f"No successful pipeline run since **{since}**"
            + (f" ({_hours(v.age)} ago)" if v.age else "")
            + f". The limit is {_hours(v.limit)} on {'game days' if game_day is not False else 'other days'}.\n\n"
            f"Check the latest runs: {actions_url}\n\n"
            "Common causes: an expired or missing secret, an NHL or odds API change, or GitHub disabling "
            "scheduled workflows (run **keepalive** once). This issue closes itself when a run succeeds.\n\n"
            "---\n_Opened by the RinkX watchdog._"
        )
        repo.create_issue(ISSUE_TITLE, body)
        if send is not None:
            try:
                send(
                    {
                        "title": "RinkX · pipeline not running",
                        "message": f"No successful run since {since}. Open the Actions page to see why.",
                        "click": actions_url,
                    }
                )
            except Exception as exc:  # the issue is the record; the push is best effort
                log.warning("watchdog push failed (%s)", type(exc).__name__)
        return "alerted"
    if issue is not None:
        repo.comment(issue, f"Recovered: the pipeline succeeded at {since}.\n\n---\n_RinkX watchdog._")
        repo.close(issue)
        return "recovered"
    return "ok"


def main(settings: Settings, now: datetime | None = None) -> int:
    if not settings.github_repository:
        print("GITHUB_REPOSITORY is not set; the watchdog runs in GitHub Actions.")
        return 2
    from rinkx.alerts import ntfy

    now = now or utcnow()
    send = ntfy.sender(settings.ntfy_topic, settings.ntfy_server) if settings.ntfy_topic else None
    url = f"https://github.com/{settings.github_repository}/actions/workflows/pipeline.yml"
    game_day = nhl_game_day(slate_date(now))
    result = watch(GhRepo(settings.github_repository), now, game_day, url, send)
    print(f"watchdog: {result} (game day: {game_day})")
    return 0
