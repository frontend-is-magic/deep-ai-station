"""Verify downloaded starter formatting outside the platform's config ancestry."""

import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from build_starters import BACKENDS, DESTINATION, ROOT


def run_prettier(node, prettier, directory, *arguments):
    return subprocess.run(  # noqa: S603 - fixed maintainer binaries and archive paths
        [node, str(prettier), *arguments],
        cwd=directory,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def verify_archive(language, node):
    filename = "agent-research.zip" if language == "agent" else f"fullstack-{language}.zip"
    # The OS temporary directory must not inherit this repository's Prettier config.
    with tempfile.TemporaryDirectory(prefix="deep-ai-starter-") as temporary:
        directory = Path(temporary).resolve()
        if directory.is_relative_to(ROOT):
            raise RuntimeError(
                "Starter verification requires a temporary directory outside the repo"
            )
        with zipfile.ZipFile(DESTINATION / filename) as archive:
            archive.extractall(directory)
        checks = [("frontend", "frontend", "src/main.tsx", ("src", "*.ts", "*.json", "*.html"))]
        if language == "typescript":
            checks.append(("backend", "typescript", "src/app.ts", ("src", "*.json")))
        for target, installed, sample, patterns in checks:
            cwd = directory / target
            prettier = ROOT / "starters" / installed / "node_modules/prettier/bin/prettier.cjs"
            config = run_prettier(node, prettier, cwd, "--find-config-path", sample)
            if (cwd / config).resolve() != directory / ".prettierrc.json":
                raise RuntimeError(f"{filename}/{target} did not resolve its own Prettier config")
            run_prettier(node, prettier, cwd, "--check", *patterns)
        print(json.dumps({"archive": filename, "format_checks": len(checks), "status": "verified"}))


def main():
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Node.js is required to verify standalone starter archives")
    try:
        for language in BACKENDS:
            verify_archive(language, node)
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stdout + error.stderr) from error


if __name__ == "__main__":
    main()
