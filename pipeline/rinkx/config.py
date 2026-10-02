"""Runtime settings, read from environment variables.

Secrets (`RINKX_DATA_KEY`, `RINKX_STORE_KEY`) come from GitHub Actions secrets in prod
and from `.rinkx/dev.env` locally. They are never written to the repo or the bundle.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal


class ConfigError(RuntimeError):
    """A required setting is missing or malformed."""


def find_repo_root(start: Path | None = None, environ: dict[str, str] | None = None) -> Path:
    """The RinkX checkout the pipeline operates on (it needs db/, config/ and .rinkx/).

    Resolved from RINKX_REPO_ROOT, else by walking up from the working directory, else from
    this file's location (editable installs). Never from site-packages: a regular
    `pip install ./pipeline` puts the code there, far from the repository files.
    """
    e = os.environ if environ is None else environ
    if e.get("RINKX_REPO_ROOT"):
        return Path(e["RINKX_REPO_ROOT"]).resolve()
    for base in (start or Path.cwd(), Path(__file__).resolve().parent):
        for d in (base, *base.parents):
            if (d / "db/schema.sql").is_file() and (d / "pipeline").is_dir():
                return d
    raise ConfigError(
        "cannot find the RinkX repository (db/schema.sql); run from inside the checkout or set RINKX_REPO_ROOT"
    )


REPO_ROOT = find_repo_root()

Env = Literal["dev", "prod"]
Backend = Literal["github", "local"]


def _decode_key(name: str, value: str | None) -> bytes | None:
    if not value:
        return None
    try:
        raw = base64.b64decode(value.strip(), validate=True)
    except binascii.Error as exc:
        raise ConfigError(f"{name} is not valid base64") from exc
    if len(raw) != 32:
        raise ConfigError(f"{name} must decode to 32 bytes (got {len(raw)})")
    return raw


@dataclass(frozen=True)
class Settings:
    env: Env
    data_key: bytes | None
    store_key: bytes | None
    keyfile: Path
    store_backend: Backend
    store_dir: Path
    store_tag: str
    github_repository: str | None
    workdir: Path
    db_dir: Path
    sources: frozenset[str]  # ingestion sources to run: "nhl", or empty ("none")
    fixtures_dir: Path | None  # serve recorded API responses instead of the network (tests/dev)
    today: date | None  # override the slate date (tests/dev)
    boxscore_limit: int  # max box scores fetched per run (raise for a one-off backfill)
    odds_api_key: str | None = None  # The Odds API (Actions secret ODDS_API_KEY); never logged

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        e = os.environ if environ is None else environ
        env = e.get("RINKX_ENV", "dev")
        if env not in ("dev", "prod"):
            raise ConfigError(f"RINKX_ENV must be 'dev' or 'prod', got {env!r}")
        backend = e.get("RINKX_STORE_BACKEND", "github" if env == "prod" else "local")
        if backend not in ("github", "local"):
            raise ConfigError(f"RINKX_STORE_BACKEND must be 'github' or 'local', got {backend!r}")
        default_keyfile = REPO_ROOT / ("config/keyfile.json" if env == "prod" else ".rinkx/keyfile.json")
        return cls(
            env=env,  # type: ignore[arg-type]
            data_key=_decode_key("RINKX_DATA_KEY", e.get("RINKX_DATA_KEY")),
            store_key=_decode_key("RINKX_STORE_KEY", e.get("RINKX_STORE_KEY")),
            keyfile=Path(e.get("RINKX_KEYFILE", default_keyfile)),
            store_backend=backend,  # type: ignore[arg-type]
            store_dir=Path(e.get("RINKX_STORE_DIR", REPO_ROOT / ".rinkx/store")),
            store_tag=e.get("RINKX_STORE_TAG", "store"),
            github_repository=e.get("GITHUB_REPOSITORY"),
            workdir=Path(e.get("RINKX_WORKDIR", REPO_ROOT / ".rinkx/work")),
            db_dir=Path(e.get("RINKX_DB_DIR", REPO_ROOT / "db")),
            sources=frozenset(x for x in e.get("RINKX_SOURCES", "nhl").split(",") if x and x != "none"),
            fixtures_dir=Path(e["RINKX_FIXTURES"]) if e.get("RINKX_FIXTURES") else None,
            today=date.fromisoformat(e["RINKX_TODAY"]) if e.get("RINKX_TODAY") else None,
            boxscore_limit=int(e.get("RINKX_BOXSCORE_LIMIT", "40")),
            odds_api_key=e.get("RINKX_ODDS_API_KEY") or None,
        )

    def require_data_key(self) -> bytes:
        if self.data_key is None:
            raise ConfigError("RINKX_DATA_KEY is not set (Actions secret DATA_KEY)")
        return self.data_key

    def require_store_key(self) -> bytes:
        if self.store_key is None:
            raise ConfigError("RINKX_STORE_KEY is not set (Actions secret STORE_KEY)")
        return self.store_key
