"""Fixed public resources work from source and from the standalone archive."""

import json
from pathlib import Path


def resource_path(name: str) -> Path:
    base = Path(__file__).resolve().parent
    local = base / name
    return local if local.is_file() else base.parent / "shared" / name


def load_fixture() -> dict:
    return json.loads(resource_path("fixtures.json").read_text(encoding="utf-8"))
