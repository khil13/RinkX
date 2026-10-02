"""ntfy delivery (https://docs.ntfy.sh/publish/): JSON publishing, so any text is safe.

The topic acts as a password; it comes from the NTFY_TOPIC secret and is never logged.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from rinkx.alerts.evaluate import Sender

DEFAULT_SERVER = "https://ntfy.sh"


def sender(topic: str, server: str = DEFAULT_SERVER, timeout: float = 10.0) -> Sender:
    def send(payload: dict[str, Any]) -> None:
        body: dict[str, Any] = {
            "topic": topic,
            "title": payload["title"],
            "message": payload["message"],
            "tags": ["ice_hockey"],
        }
        if payload.get("click"):
            body["click"] = payload["click"]
        req = urllib.request.Request(
            server.rstrip("/"),
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"HTTP {resp.status}")

    return send
