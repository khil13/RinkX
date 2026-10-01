"""Fail the build if user-facing code uses hype or certainty language.

Scans the web app and pipeline sources (everything that can end up on screen).
A line can opt out with the marker  copy-lint: allow  (used only for quoting the rule itself).
Run: python scripts/lint_copy.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / "web/src", ROOT / "pipeline/rinkx"]
EXTS = {".ts", ".tsx", ".py", ".html", ".json"}
ALLOW = "copy-lint: allow"

BANNED: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bLOCKS?\b"), "LOCK"),  # all-caps tout usage; the app's "Lock" button is fine
    (re.compile(r"\block of the (day|night|week)\b", re.I), "lock of the day"),
    (re.compile(r"\bguaranteed?\b", re.I), "guarantee(d)"),
    (re.compile(r"\bfree money\b", re.I), "free money"),
    (re.compile(r"\bcan'?t lose\b|\bcannot lose\b", re.I), "can't lose"),
    (re.compile(r"\bsure thing\b", re.I), "sure thing"),
    (re.compile(r"\brisk[- ]free\b", re.I), "risk-free"),
    (re.compile(r"\bcan'?t miss\b", re.I), "can't miss"),
    (re.compile(r"\bmax bet\b", re.I), "max bet"),
    (re.compile(r"\bhammer (it|this|that)\b", re.I), "hammer it"),
]


def scan(paths: list[Path]) -> list[str]:
    problems = []
    for base in paths:
        for f in sorted(base.rglob("*")):
            if f.suffix not in EXTS or not f.is_file():
                continue
            for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if ALLOW in line:
                    continue
                for pattern, label in BANNED:
                    if pattern.search(line):
                        problems.append(f"{f.relative_to(ROOT)}:{n}: banned phrase '{label}': {line.strip()}")
    return problems


def main() -> int:
    problems = scan(SCAN)
    for p in problems:
        print(p)
    if problems:
        print(f"\n{len(problems)} banned phrase(s). RinkX presents probabilities, never certainties.")
        return 1
    print("copy lint: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
