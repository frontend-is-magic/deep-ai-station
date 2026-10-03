"""同一份公开资料支持源码目录和解压后的独立实验。"""

import json
from pathlib import Path


def fixture_path(name: str) -> Path:
    local = Path(__file__).resolve().parent / name
    return local if local.is_file() else local.parent.parent / "shared" / name


def load_fixture() -> dict:
    return json.loads(fixture_path("fixtures.json").read_text(encoding="utf-8"))
