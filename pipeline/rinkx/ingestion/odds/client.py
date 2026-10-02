"""The Odds API v4 client.

* The API key travels only in the query string and is redacted from every error message, so
  it can't leak into Actions logs or the store.
* Every metered response's quota headers (x-requests-last / -remaining / -used) are returned,
  so the budgeter works from the account's real balance, not an estimate.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from rinkx.ingestion.http import USER_AGENT

BASE = "https://api.the-odds-api.com/v4"
_KEY_RE = re.compile(r"(apiKey=)[^&\s]+")


def redact(text: str) -> str:
    return _KEY_RE.sub(r"\1***", text)


class OddsError(RuntimeError):
    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{redact(url)}: {redact(message)}")
        self.status = status


@dataclass(frozen=True)
class Usage:
    charged: int | None  # x-requests-last
    remaining: int | None  # x-requests-remaining
    used: int | None  # x-requests-used


class Transport(Protocol):
    calls: int

    def get(self, url: str) -> tuple[Any, dict[str, str]]: ...


class UrllibTransport:
    def __init__(
        self,
        *,
        min_interval_s: float = 1.0,
        timeout_s: float = 20.0,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s, self.timeout_s, self.retries, self.sleep = min_interval_s, timeout_s, retries, sleep
        self.calls = 0
        self._last = 0.0

    def get(self, url: str) -> tuple[Any, dict[str, str]]:
        for attempt in range(self.retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    headers = {k.lower(): v for k, v in resp.headers.items()}
                    return json.loads(resp.read()), headers
            except urllib.error.HTTPError as exc:
                # 401 bad key, 422 bad parameter, 429 out of credits or rate limited: never retry
                # a metered call blindly except on server errors.
                if exc.code < 500 or attempt == self.retries:
                    raise OddsError(url, f"HTTP {exc.code}", exc.code) from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == self.retries:
                    raise OddsError(url, str(exc)) from None
            self.sleep(2.0 * (2**attempt))
        raise AssertionError("unreachable")


def _int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


class OddsClient:
    def __init__(self, api_key: str, transport: Transport, sport: str = "icehockey_nhl", regions: str = "us"):
        self.api_key, self.transport, self.sport, self.regions = api_key, transport, sport, regions

    def _get(self, path: str, **params: str) -> tuple[Any, Usage]:
        q = urllib.parse.urlencode({**params, "apiKey": self.api_key})
        data, headers = self.transport.get(f"{BASE}{path}?{q}")
        usage = Usage(
            _int(headers.get("x-requests-last")),
            _int(headers.get("x-requests-remaining")),
            _int(headers.get("x-requests-used")),
        )
        return data, usage

    def events(self) -> tuple[list[dict[str, Any]], Usage]:
        """Upcoming events (no odds). Not metered by the API."""
        data, usage = self._get(f"/sports/{self.sport}/events", dateFormat="iso")
        if not isinstance(data, list):
            raise OddsError(f"{BASE}/sports/{self.sport}/events", "expected a list of events")
        return data, usage

    def game_odds(self, markets: list[str], bookmakers: list[str]) -> tuple[list[dict[str, Any]], Usage]:
        data, usage = self._get(
            f"/sports/{self.sport}/odds",
            markets=",".join(markets),
            bookmakers=",".join(bookmakers),
            oddsFormat="american",
            dateFormat="iso",
        )
        if not isinstance(data, list):
            raise OddsError(f"{BASE}/sports/{self.sport}/odds", "expected a list of events")
        return data, usage

    def event_odds(self, event_id: str, markets: list[str], bookmakers: list[str]) -> tuple[dict[str, Any], Usage]:
        data, usage = self._get(
            f"/sports/{self.sport}/events/{event_id}/odds",
            markets=",".join(markets),
            bookmakers=",".join(bookmakers),
            oddsFormat="american",
            dateFormat="iso",
        )
        if not isinstance(data, dict):
            raise OddsError(f"{BASE}/sports/{self.sport}/events/{event_id}/odds", "expected an event object")
        return data, usage


def credits_for(markets: list[str], bookmakers: list[str]) -> int:
    """The API's documented cost: markets x regions, where each group of up to 10 bookmakers
    counts as one region. Used for planning; the charged amount comes from the headers."""
    return len(markets) * max(1, -(-len(bookmakers) // 10))
