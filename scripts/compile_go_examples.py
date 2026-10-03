"""Compile committed Go references without running programs or tests."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from backend.curriculum import TRACKS

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "go-reference"


def main():
    compiler = shutil.which("go") or str(ROOT / ".tools" / "go" / "bin" / "go")
    if not Path(compiler).is_file():
        raise RuntimeError("Go compiler unavailable")
    environment = {**os.environ, "CGO_ENABLED": "0", "GOTOOLCHAIN": "local"}
    with tempfile.TemporaryDirectory(prefix="deep-ai-station-go-references-") as directory:
        for lesson in TRACKS[1]["lessons"]:
            if not re.fullmatch(r"[a-z0-9-]+", lesson["id"]):
                raise ValueError("Invalid reference ID")
            folder = Path(directory) / lesson["id"]
            folder.mkdir()
            source = folder / "reference_test.go"
            source.write_text(lesson["snippets"]["go"])
            # -c creates the test binary but never starts it. No API/user source
            # is read here; dependencies remain locked and readonly.
            subprocess.run(  # noqa: S603 - trusted repository references, no shell
                [
                    compiler,
                    "test",
                    "-c",
                    "-mod=readonly",
                    "-o",
                    str(folder / "compile-only"),
                    str(source),
                ],
                cwd=MODULE,
                env=environment,
                check=True,
                timeout=120,
            )
    print("Go references compiled: 24; no programs or tests executed")


if __name__ == "__main__":
    main()
