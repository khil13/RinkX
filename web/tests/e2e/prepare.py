"""Build the sites the e2e tests run against, using the real pipeline.

  .sites/league      dist/ + the real pipeline replaying recorded NHL responses (2026-03-10)
  .sites/empty       dist/ + a configured bundle with no data sources connected
  .sites/setup       dist/ + a manifest-only bundle (no keys yet)

Run from web/ after `vite build`:  python tests/e2e/prepare.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parents[1]
REPO = WEB.parent
sys.path.insert(0, str(REPO / "pipeline"))

from rinkx import crypto  # noqa: E402
from rinkx.config import Settings  # noqa: E402
from rinkx.pipeline import run  # noqa: E402

PASSPHRASE = "e2e passphrase with several words"
SITES = HERE / ".sites"


def site(name: str) -> Path:
    dest = SITES / name
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(WEB / "dist", dest)
    return dest


def main() -> None:
    if not (WEB / "dist/index.html").is_file():
        raise SystemExit("run `vite build` first")
    SITES.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="rinkx-e2e-"))
    base = {
        "RINKX_ENV": "prod",
        "RINKX_STORE_BACKEND": "local",
        "RINKX_STORE_DIR": str(tmp / "remote"),
        "RINKX_WORKDIR": str(tmp / "work"),
        "RINKX_DB_DIR": str(REPO / "db"),
        "RINKX_SOURCES": "none",
    }

    # Configured sites share production keyfile parameters (600k iterations) and prod env.
    data_key, store_key = crypto.new_key(), crypto.new_key()
    keyfile = tmp / "keyfile.json"
    keyfile.write_text(json.dumps(crypto.make_keyfile(PASSPHRASE, data_key)))
    keys = {
        "RINKX_KEYFILE": str(keyfile),
        "RINKX_DATA_KEY": crypto.b64e(data_key),
        "RINKX_STORE_KEY": crypto.b64e(store_key),
    }

    # League site: the real pipeline ingesting the recorded NHL responses for 2026-03-10.
    run(
        Settings.from_env(
            base
            | keys
            | {
                "RINKX_SOURCES": "nhl",
                "RINKX_FIXTURES": str(REPO / "pipeline/tests/fixtures/nhl"),
                "RINKX_TODAY": "2026-03-10",
                "RINKX_BOXSCORE_LIMIT": "100",
                "RINKX_STORE_DIR": str(tmp / "remote-league"),
            }
        ),
        site("league") / "data",
    )

    # Empty site: configured, but no data source has ever run.
    run(Settings.from_env(base | keys | {"RINKX_STORE_DIR": str(tmp / "remote-empty")}), site("empty") / "data")

    # Unconfigured site: what Pages serves before the owner completes setup.
    setup = site("setup")
    run(Settings.from_env(base | {"RINKX_KEYFILE": str(tmp / "absent.json")}), setup / "data")

    (SITES / "secrets.json").write_text(json.dumps({"passphrase": PASSPHRASE}))
    shutil.rmtree(tmp)
    print(f"e2e sites ready in {SITES}")


if __name__ == "__main__":
    main()
