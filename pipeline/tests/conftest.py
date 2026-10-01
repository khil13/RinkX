import json
from pathlib import Path

import pytest

from rinkx import crypto
from rinkx.config import REPO_ROOT, Settings

PASSPHRASE = "test passphrase that is long enough"


@pytest.fixture
def keys(tmp_path: Path) -> dict[str, object]:
    data_key, store_key = crypto.new_key(), crypto.new_key()
    keyfile = tmp_path / "keyfile.json"
    keyfile.write_text(json.dumps(crypto.make_keyfile(PASSPHRASE, data_key, iterations=1000)))
    return {"data_key": data_key, "store_key": store_key, "keyfile": keyfile}


def make_settings(tmp_path: Path, keys: dict[str, object] | None, env: str = "dev") -> Settings:
    e = {
        "RINKX_ENV": env,
        "RINKX_STORE_BACKEND": "local",
        "RINKX_STORE_DIR": str(tmp_path / "remote"),
        "RINKX_WORKDIR": str(tmp_path / "work"),
        "RINKX_DB_DIR": str(REPO_ROOT / "db"),
        "RINKX_SOURCES": "none",
        "RINKX_KEYFILE": str(keys["keyfile"] if keys else tmp_path / "missing-keyfile.json"),
    }
    if keys:
        e["RINKX_DATA_KEY"] = crypto.b64e(keys["data_key"])  # type: ignore[arg-type]
        e["RINKX_STORE_KEY"] = crypto.b64e(keys["store_key"])  # type: ignore[arg-type]
    return Settings.from_env(e)
