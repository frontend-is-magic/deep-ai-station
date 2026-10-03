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

STORAGE_FILES = {
    "python": ["app.py", "repository.py", "test_app.py", "pyproject.toml", "uv.lock"],
    "typescript": [
        "src/main.ts",
        "src/request.ts",
        "src/repository.ts",
        "src/repository.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    ],
    "go": ["main.go", "request.go", "repository.go", "repository_test.go", "go.mod", "go.sum"],
}
STORAGE_SHARED = [
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "contract-cases.json",
    "migrations/001.sql",
    "migrations/002.sql",
]
AUTH_FILES = {
    "python": [
        "app.py",
        "auth.py",
        "errors.py",
        "repository.py",
        "resources.py",
        "service.py",
        "test_app.py",
        "pyproject.toml",
        "uv.lock",
    ],
    "typescript": [
        "src/app.ts",
        "src/auth.ts",
        "src/repository.ts",
        "src/request.ts",
        "src/service.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    ],
    "go": ["app.go", "store.go", "main.go", "app_test.go", "go.mod", "go.sum"],
}
AUTH_SHARED = [
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "fixtures.json",
    "contract-cases.json",
]
UPLOAD_FILES = {
    "python": [
        "app.py",
        "auth.py",
        "errors.py",
        "repository.py",
        "resources.py",
        "service.py",
        "test_app.py",
        "pyproject.toml",
        "uv.lock",
    ],
    "typescript": [
        "src/app.ts",
        "src/auth.ts",
        "src/repository.ts",
        "src/request.ts",
        "src/service.ts",
        "src/resources.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    ],
    "go": ["app.go", "auth.go", "repository.go", "main.go", "app_test.go", "go.mod", "go.sum"],
}
UPLOAD_SHARED = [
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "fixtures.json",
    "contract-cases.json",
]
LABS = {
    "api-contract": (FILES, SHARED),
    "sqlite-storage": (STORAGE_FILES, STORAGE_SHARED),
    "session-authorization": (AUTH_FILES, AUTH_SHARED),
    "text-upload": (UPLOAD_FILES, UPLOAD_SHARED),
}


def bundle(language, lab="api-contract"):
    source = ROOT / "labs" / lab
    members, shared = LABS[lab]
    files = {name: (source / language / name).read_bytes() for name in members[language]}
    files.update({name: (source / "shared" / name).read_bytes() for name in shared})
    files["manifest.json"] = (source / "manifest.json").read_bytes()
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
    for lab, (languages, _) in LABS.items():
        for language in languages:
            expected = bundle(language, lab)
            path = DESTINATION / f"{lab}-{language}.zip"
            if args.check:
                if not path.exists() or path.read_bytes() != expected:
                    raise SystemExit(f"Course lab archive is stale: {path.name}")
            else:
                path.write_bytes(expected)
            print(f"{path.name}: {len(expected)} bytes, {'verified' if args.check else 'built'}")


if __name__ == "__main__":
    main()
