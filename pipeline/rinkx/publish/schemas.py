"""The published data contract (docs/03-api.md). Mirrored by web/src/lib/data/types.ts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

DataStatus = Literal["live", "stale", "unavailable", "synthetic"]
FeedState = Literal["ok", "stale", "failed", "unavailable"]

MANIFEST_SCHEMA = 1


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeedStatus(Strict):
    code: str
    name: str
    state: FeedState
    last_success_at: str | None
    reason: str | None = None


class FileEntry(Strict):
    sha256: str
    bytes: int


class BuildInfo(Strict):
    app_version: str
    git_sha: str | None
    run_url: str | None


class Manifest(Strict):
    schema_version: int = MANIFEST_SCHEMA
    generated_at: str
    env: Literal["dev", "prod"]
    configured: bool
    missing_setup: list[str]
    build: BuildInfo
    feeds: list[FeedStatus]
    files: dict[str, FileEntry]


class Meta(Strict):
    generated_at: str
    data_status: DataStatus
    oldest_input_at: str | None
    sources: list[str]
    model_versions: dict[str, str]


class Envelope(Strict):
    data: Any
    meta: Meta
