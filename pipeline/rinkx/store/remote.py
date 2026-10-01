"""Where the encrypted store lives between runs.

prod: assets on a GitHub Release (default tag `store`), managed with the `gh` CLI.
dev/tests: a local directory with the same semantics.

Each upload is a new, timestamped asset; older ones are deleted only after the new one is
uploaded, so a failed run can never leave the store missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from rinkx import crypto
from rinkx.config import ConfigError, Settings

ASSET_PREFIX = "rinkx-"
ASSET_SUFFIX = ".db.enc"
KEEP_VERSIONS = 10


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
    for old in assets[:-KEEP_VERSIONS]:
        backend.delete(old)
    return name
