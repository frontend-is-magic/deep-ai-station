"""Build reproducible, self-contained course labs from a fixed file whitelist."""

import argparse
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "labs" / "api-contract"
DESTINATION = ROOT / "public" / "labs"
FILES = {
    "python": ["app.py", "service.py", "repository.py", "test_app.py", "pyproject.toml", "uv.lock"],
    "typescript": [
        "src/app.ts",
        "src/service.ts",
        "src/repository.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    ],
    "go": ["app.go", "service.go", "repository.go", "main.go", "app_test.go", "go.mod", "go.sum"],
}
SHARED = [
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "lessons.json",
    "contract-cases.json",
]


def bundle(language):
    files = {name: (SOURCE / language / name).read_bytes() for name in FILES[language]}
    files.update({name: (SOURCE / "shared" / name).read_bytes() for name in SHARED})
    files["manifest.json"] = (SOURCE / "manifest.json").read_bytes()
    files[".prettierrc.json"] = (ROOT / ".prettierrc.json").read_bytes()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.check:
        DESTINATION.mkdir(parents=True, exist_ok=True)
    for language in FILES:
        expected = bundle(language)
        path = DESTINATION / f"api-contract-{language}.zip"
        if args.check:
            if not path.exists() or path.read_bytes() != expected:
                raise SystemExit(f"Course lab archive is stale: {path.name}")
        else:
            path.write_bytes(expected)
        print(f"{path.name}: {len(expected)} bytes, {'verified' if args.check else 'built'}")


if __name__ == "__main__":
    main()
