"""Fetching JSON from external APIs.

`HttpFetcher` is polite by construction: one request at a time, a minimum gap between
requests, a descriptive User-Agent, bounded retries with backoff on 429/5xx. Tests use
`FixtureFetcher`, which serves recorded responses keyed by URL.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Protocol

USER_AGENT = "RinkX/0.1 (personal research project; https://github.com/khil13/RinkX)"


class FetchError(RuntimeError):
    def __init__(self, url: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.status = status


class Fetcher(Protocol):
    calls: int

    def get_json(self, url: str) -> Any: ...


class HttpFetcher:
    def __init__(
        self,
        *,
        min_interval_s: float = 0.5,
        timeout_s: float = 20.0,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.timeout_s = timeout_s
        self.retries = retries
        self.sleep = sleep
        self.calls = 0
        self._last = 0.0

    def get_json(self, url: str) -> Any:
        for attempt in range(self.retries + 1):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if not retryable or attempt == self.retries:
                    raise FetchError(url, f"HTTP {exc.code}", exc.code) from exc
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt == self.retries:
                    raise FetchError(url, str(exc)) from exc
            self.sleep(2.0 * (2**attempt))
        raise AssertionError("unreachable")


class FixtureFetcher:
    """Serves recorded responses from a fixtures directory with an index.json (url -> file)."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        index = json.loads((directory / "index.json").read_text())
        self.by_url: dict[str, str] = {f["url"]: f["file"] for f in index["files"]}
        self.calls = 0

    def get_json(self, url: str) -> Any:
        self.calls += 1
        name = self.by_url.get(url)
        if name is None:
            raise FetchError(url, "no recorded fixture", 404)
        return json.loads((self.directory / name).read_text())


class ReplayFetcher(FixtureFetcher):
    """Offline replay of the recorded 2026-03-10 snapshot, for tests and local dev only.

    "Current" endpoints are served from their dated recordings. Anything not recorded fails
    exactly like an outage would, so the pipeline's handling of missing data is exercised
    rather than papered over.
    """

    ALIASES: ClassVar[dict[str, str]] = {
        "https://api-web.nhle.com/v1/standings/now": "https://api-web.nhle.com/v1/standings/2026-03-10",
        "https://api-web.nhle.com/v1/roster/BOS/current": "https://api-web.nhle.com/v1/roster/BOS/20252026",
        "https://api-web.nhle.com/v1/roster/LAK/current": "https://api-web.nhle.com/v1/roster/LAK/20252026",
        "https://api-web.nhle.com/v1/roster/TOR/current": "https://api-web.nhle.com/v1/roster/TOR/20252026",
    }

    def get_json(self, url: str) -> Any:
        return super().get_json(self.ALIASES.get(url, url))
