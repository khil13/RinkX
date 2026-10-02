"""Record additional NHL API responses as test fixtures (run by record-nhl-fixtures.yml).

Uses the pipeline's own URL builders so recorded URLs match what the pipeline requests.
Appends to pipeline/tests/fixtures/nhl/index.json, which the bash recorder writes first.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from rinkx.ingestion.nhl import urls  # noqa: E402

OUT = ROOT / "pipeline/tests/fixtures/nhl"
GAME_ID = 2025021012
GAME_DATE = "2026-03-10"
UA = "RinkX fixture recorder (personal project)"


def fetch(url: str) -> object:
    time.sleep(1)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> None:
    index = json.loads((OUT / "index.json").read_text())
    files = index["files"]

    def save(name: str, url: str) -> object | None:
        try:
            data = fetch(url)
        except Exception as exc:  # record what failed; don't hide it
            print(f"::warning::{url} -> {exc}")
            return None
        (OUT / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        files.append({"file": f"{name}.json", "url": url})
        print(f"recorded {name}")
        return data

    save(f"pbp_{GAME_ID}", urls.play_by_play(GAME_ID))
    save(f"shifts_{GAME_ID}", urls.shift_chart(GAME_ID))
    for entity, report in [
        ("skater", "summary"),
        ("skater", "timeonice"),
        ("skater", "realtime"),
        ("skater", "faceoffwins"),
        ("goalie", "summary"),
    ]:
        start = 0
        while True:
            data = save(
                f"stats_{entity}_{report}_{GAME_DATE}_{start}", urls.game_report(entity, report, GAME_DATE, start)
            )
            if not isinstance(data, dict):
                break
            start += urls.PAGE_SIZE
            if start >= int(data.get("total", 0)):
                break

    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    main()
