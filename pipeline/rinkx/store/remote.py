"""Where the encrypted store lives between runs.

prod: assets on a GitHub Release (default tag `store`), managed with the `gh` CLI.
dev/tests: a local directory with the same semantics.

Each upload is a new, timestamped asset; older ones are deleted only after the new one is
uploaded, so a failed run can never leave the store missing. Kept: the newest KEEP_VERSIONS,
plus the newest version of each of the last WEEKLY_SNAPSHOTS ISO weeks (weekly snapshots), so a
problem noticed days later can still be rolled back.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from rinkx import crypto
from rinkx.config import ConfigError, Settings

ASSET_PREFIX = "rinkx-"
ASSET_SUFFIX = ".db.enc"
KEEP_VERSIONS = 10
WEEKLY_SNAPSHOTS = 8


class StoreError(RuntimeError):
    pass


class Backend(Protocol):
    def list_assets(self) -> list[str]: ...
    def download(self, name: str, dest: Path) -> None: ...
    def upload(self, src: Path) -> None: ...
    def delete(self, name: str) -> None: ...


class LocalBackend:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def list_assets(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_file())

    def download(self, name: str, dest: Path) -> None:
        shutil.copyfile(self.root / name, dest)

    def upload(self, src: Path) -> None:
        shutil.copyfile(src, self.root / src.name)

    def delete(self, name: str) -> None:
        (self.root / name).unlink()


class GithubReleaseBackend:
    def __init__(self, repo: str, tag: str) -> None:
        self.repo = repo
        self.tag = tag

    def _gh(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["gh", *args, "--repo", self.repo], capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise StoreError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc

    def _ensure_release(self) -> None:
        if self._gh("release", "view", self.tag, check=False).returncode == 0:
            return
        self._gh(
            "release",
            "create",
            self.tag,
            "--title",
            "RinkX data store (encrypted)",
            "--notes",
            "Encrypted SQLite store written by the RinkX pipeline. Not a software release.",
            "--prerelease",
        )

    def list_assets(self) -> list[str]:
        proc = self._gh("release", "view", self.tag, "--json", "assets", check=False)
        if proc.returncode != 0:
            if "release not found" in proc.stderr.lower():
                return []
            raise StoreError(f"cannot read release {self.tag!r}: {proc.stderr.strip()}")
        return sorted(a["name"] for a in json.loads(proc.stdout)["assets"])

    def download(self, name: str, dest: Path) -> None:
        self._gh("release", "download", self.tag, "--pattern", name, "--output", str(dest), "--clobber")

    def upload(self, src: Path) -> None:
        self._ensure_release()
        self._gh("release", "upload", self.tag, str(src))

    def delete(self, name: str) -> None:
        self._gh("release", "delete-asset", self.tag, name, "--yes")


def backend_for(settings: Settings) -> Backend:
    if settings.store_backend == "local":
        return LocalBackend(settings.store_dir)
    if not settings.github_repository:
        raise ConfigError("GITHUB_REPOSITORY is not set; needed for the GitHub Release store")
    return GithubReleaseBackend(settings.github_repository, settings.store_tag)


def _store_assets(backend: Backend) -> list[str]:
    return [n for n in backend.list_assets() if n.startswith(ASSET_PREFIX) and n.endswith(ASSET_SUFFIX)]


def pull(backend: Backend, store_key: bytes, dest: Path) -> str | None:
    """Download and decrypt the newest store to `dest`. Returns its asset name, or None if no
    store exists yet. A store that exists but cannot be decrypted is an error, never a reset."""
    assets = _store_assets(backend)
    if not assets:
        return None
    latest = assets[-1]
    tmp = dest.with_suffix(".download")
    backend.download(latest, tmp)
    try:
        dest.write_bytes(crypto.decrypt_store(tmp.read_bytes(), store_key))
    finally:
        tmp.unlink(missing_ok=True)
    return latest


def push(backend: Backend, store_key: bytes, plaintext_db: Path, *, now: datetime | None = None) -> str:
    """Encrypt and upload a new store version, then prune to the newest KEEP_VERSIONS."""
    # Microsecond timestamps sort chronologically as plain strings, which `pull` relies on.
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"{ASSET_PREFIX}{stamp}{ASSET_SUFFIX}"
    if name in set(_store_assets(backend)):
        raise StoreError(f"{name} already exists")
    out = plaintext_db.parent / name
    out.write_bytes(crypto.encrypt_store(plaintext_db.read_bytes(), store_key))
    try:
        backend.upload(out)
    finally:
        out.unlink(missing_ok=True)
    assets = _store_assets(backend)
    if name not in assets:
        raise StoreError(f"upload of {name} not visible; refusing to prune old versions")
    keep = retained(assets, now or datetime.now(UTC))
    for old in assets:
        if old not in keep:
            backend.delete(old)
    return name


def stamp_of(name: str) -> datetime:
    return datetime.strptime(name[len(ASSET_PREFIX) : -len(ASSET_SUFFIX)], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)


def retained(assets: list[str], now: datetime) -> set[str]:
    """The newest KEEP_VERSIONS, plus the newest version of each of the last WEEKLY_SNAPSHOTS ISO weeks."""
    ordered = sorted(assets)
    keep = set(ordered[-KEEP_VERSIONS:])
    weeks: dict[tuple[int, int], str] = {}
    oldest = now - timedelta(weeks=WEEKLY_SNAPSHOTS)
    for a in ordered:
        t = stamp_of(a)
        if t >= oldest:
            iso = t.isocalendar()
            weeks[(iso.year, iso.week)] = a  # ordered: the last one seen is the newest that week
    keep.update(weeks.values())
    return keep


def versions(backend: Backend, now: datetime) -> list[dict[str, Any]]:
    """Stored versions, newest first, for the Admin page."""
    assets = sorted(_store_assets(backend), reverse=True)
    recent = set(sorted(assets)[-KEEP_VERSIONS:])
    return [
        {"name": a, "at": stamp_of(a).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": "recent" if a in recent else "weekly"}
        for a in assets
    ]


def restore(backend: Backend, store_key: bytes, name: str, workdir: Path, *, now: datetime | None = None) -> str:
    """Make an older version the current one: decrypt it (proving the key and file are good), check
    it, and upload it again as a new version. Nothing is deleted except by normal rotation."""
    if name not in set(_store_assets(backend)):
        raise StoreError(f"no stored version named {name!r}")
    db = workdir / "restore.db"
    tmp = workdir / "restore.download"
    backend.download(name, tmp)
    try:
        db.write_bytes(crypto.decrypt_store(tmp.read_bytes(), store_key))
    finally:
        tmp.unlink(missing_ok=True)
    try:
        check = integrity(db)
        if check["integrity"] != "ok":
            raise StoreError(f"{name} failed its integrity check: {check['integrity']}")
        return push(backend, store_key, db, now=now)
    finally:
        db.unlink(missing_ok=True)


COUNTED = ("games", "players", "player_game_stats", "prop_lines", "predictions", "model_results")


def integrity(db: Path) -> dict[str, Any]:
    conn = sqlite3.connect(db)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in COUNTED if t in tables}
        return {"integrity": ok, "rows": counts}
    finally:
        conn.close()


@dataclass
class DrillResult:
    ok: bool
    version: str | None
    age_days: float | None
    detail: dict[str, Any] = field(default_factory=dict)


def drill(backend: Backend, store_key: bytes, workdir: Path, db_dir: Path, now: datetime) -> DrillResult:
    """Restore drill, read-only: the OLDEST kept version must download, decrypt, pass SQLite's
    integrity check, and migrate to the current schema. Nothing is uploaded."""
    from rinkx.store.db import connect, migrate  # local import: db imports nothing from here

    assets = _store_assets(backend)
    if not assets:
        return DrillResult(True, None, None, {"note": "no stored versions yet"})
    oldest = sorted(assets)[0]
    age = (now - stamp_of(oldest)).total_seconds() / 86400
    db = workdir / "drill.db"
    tmp = workdir / "drill.download"
    try:
        backend.download(oldest, tmp)
        db.write_bytes(crypto.decrypt_store(tmp.read_bytes(), store_key))
        check = integrity(db)
        conn = connect(db)
        try:
            version = migrate(conn, db_dir)
        finally:
            conn.close()
        ok = check["integrity"] == "ok"
        return DrillResult(ok, oldest, age, check | {"schema": version})
    except Exception as exc:  # reported, never raised: a failed drill must not stop the run
        return DrillResult(False, oldest, age, {"error": type(exc).__name__})
    finally:
        tmp.unlink(missing_ok=True)
        db.unlink(missing_ok=True)
