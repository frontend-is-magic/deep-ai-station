"""Build fixed, public maintainer starters. No user paths or code are accepted."""

import argparse
import io
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "starters"
DESTINATION = ROOT / "public" / "starters"
FRONTEND = [
    "package.json",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "vite.config.ts",
    "index.html",
    "src/main.tsx",
    "src/style.css",
    "src/components/ui/button.tsx",
]
BACKENDS = {
    "python": ["app.py", "test_app.py", "pyproject.toml", "uv.lock"],
    "typescript": [
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "src/app.ts",
        "src/app.test.ts",
        "src/server.ts",
    ],
    "go": ["main.go", "main_test.go", "go.mod", "go.sum"],
}


def bundle(language):
    files = {f"frontend/{name}": (SOURCE / "frontend" / name).read_bytes() for name in FRONTEND}
    files.update(
        {f"backend/{name}": (SOURCE / language / name).read_bytes() for name in BACKENDS[language]}
    )
    for name in ("documents.json", "contract-cases.json"):
        files[f"backend/{name}"] = (SOURCE / "shared" / name).read_bytes()
    for name in ("AGENTS.md", "EVIDENCE.md", ".gitignore"):
        files[name] = (SOURCE / "shared" / name).read_bytes()
    files["README.md"] = (SOURCE / "README.md").read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2026, 10, 3, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    for language in BACKENDS:
        for name in ("documents.json", "contract-cases.json"):
            expected = (SOURCE / "shared" / name).read_bytes()
            target = SOURCE / language / name
            if arguments.check:
                if target.read_bytes() != expected:
                    raise SystemExit(f"Shared fixture mismatch: {language}/{name}")
            else:
                target.write_bytes(expected)
        output = DESTINATION / f"fullstack-{language}.zip"
        expected = bundle(language)
        if arguments.check:
            if not output.is_file() or output.read_bytes() != expected:
                raise SystemExit(f"Starter archive mismatch: {language}")
        else:
            DESTINATION.mkdir(parents=True, exist_ok=True)
            output.write_bytes(expected)
        print(
            json.dumps(
                {
                    "language": language,
                    "bytes": len(expected),
                    "status": "verified" if arguments.check else "generated",
                }
            )
        )


if __name__ == "__main__":
    main()
