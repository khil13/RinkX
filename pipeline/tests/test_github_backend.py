"""Exercise GithubReleaseBackend against a fake `gh` that emulates releases in a directory.

This checks the exact gh subcommands and flags the backend sends, without touching GitHub.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

from rinkx import crypto
from rinkx.config import REPO_ROOT
from rinkx.store import remote
from rinkx.store.db import connect, migrate

FAKE_GH = """#!{python}
import json, os, shutil, sys
from pathlib import Path

root = Path(os.environ["FAKE_GH_DIR"])
args = sys.argv[1:]
log = root / "calls.log"
with log.open("a") as f:
    f.write(" ".join(args) + "\\n")

def opt(name):
    return args[args.index(name) + 1]

assert args[0] == "release", args
assert opt("--repo") == "khil13/RinkX", args
cmd, tag = args[1], args[2]
rel = root / "releases" / tag
if cmd == "view":
    if not rel.is_dir():
        print("release not found", file=sys.stderr); sys.exit(1)
    if "--json" in args:
        assert opt("--json") == "assets"
    print(json.dumps({{"assets": [{{"name": p.name}} for p in sorted(rel.iterdir())]}}))
elif cmd == "create":
    assert "--prerelease" in args and "--title" in args and "--notes" in args
    rel.mkdir(parents=True)
elif cmd == "upload":
    assert rel.is_dir(), "upload to missing release"
    src = Path(args[3]); shutil.copyfile(src, rel / src.name)
elif cmd == "download":
    assert "--clobber" in args
    shutil.copyfile(rel / opt("--pattern"), opt("--output"))
elif cmd == "delete-asset":
    assert "--yes" in args
    (rel / args[3]).unlink()
else:
    sys.exit(f"unexpected command {{cmd}}")
"""


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(FAKE_GH.format(python=sys.executable))
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    state = tmp_path / "gh-state"
    state.mkdir()
    monkeypatch.setenv("FAKE_GH_DIR", str(state))
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return state


def test_release_backend_roundtrip(tmp_path: Path, fake_gh: Path):
    backend = remote.GithubReleaseBackend("khil13/RinkX", "store")
    key = crypto.new_key()
    assert backend.list_assets() == []  # release doesn't exist yet
    assert remote.pull(backend, key, tmp_path / "x.db") is None

    db = tmp_path / "a.db"
    conn = connect(db)
    migrate(conn, REPO_ROOT / "db")
    conn.close()
    first = remote.push(backend, key, db)  # creates the release, then uploads
    second = remote.push(backend, key, db)
    assert backend.list_assets() == sorted([first, second])

    out = tmp_path / "pulled.db"
    assert remote.pull(backend, key, out) == second
    assert connect(out).execute("SELECT count(*) FROM markets").fetchone()[0] == 19

    calls = (fake_gh / "calls.log").read_text().splitlines()
    assert sum(c.startswith("release create store") for c in calls) == 1


def test_release_backend_prunes_after_upload(tmp_path: Path, fake_gh: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(remote, "KEEP_VERSIONS", 2)
    backend = remote.GithubReleaseBackend("khil13/RinkX", "store")
    db = tmp_path / "a.db"
    migrate(connect(db), REPO_ROOT / "db")
    key = crypto.new_key()
    names = [remote.push(backend, key, db) for _ in range(4)]
    assert backend.list_assets() == names[-2:]
