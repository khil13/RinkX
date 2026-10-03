"""CI check: every workflow with a `schedule:` trigger is re-enabled by keepalive.yml.

GitHub disables scheduled workflows in public repositories after 60 days without repository
activity. keepalive.yml re-enables them weekly, but only the ones it lists; a new scheduled
workflow that isn't listed would silently stop after 60 idle days. This is that simulation:
it fails if any scheduled workflow would be left out.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def scheduled() -> set[str]:
    out = set()
    for p in sorted(WORKFLOWS.glob("*.yml")):
        doc = yaml.safe_load(p.read_text())
        on = doc.get("on", doc.get(True, {}))  # PyYAML reads a bare `on:` key as True
        if isinstance(on, dict) and "schedule" in on:
            out.add(p.name)
    return out


def kept_alive() -> set[str]:
    text = (WORKFLOWS / "keepalive.yml").read_text()
    m = re.search(r"for wf in ([^;]+);", text)
    return set(m.group(1).split()) if m else set()


def main() -> int:
    missing = scheduled() - kept_alive()
    if missing:
        print(f"keepalive.yml does not re-enable: {', '.join(sorted(missing))}")
        return 1
    print(f"keepalive covers every scheduled workflow: {', '.join(sorted(scheduled()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
