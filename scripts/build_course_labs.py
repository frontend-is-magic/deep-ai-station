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
        "sqlite_repository.py",
        "upload_policy.py",
        "test_storage.py",
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
        "src/sqlite-repository.ts",
        "src/storage.ts",
        "src/storage.test.ts",
        "src/request.ts",
        "src/service.ts",
        "src/resources.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    ],
    "go": [
        "app.go",
        "auth.go",
        "repository.go",
        "main.go",
        "app_test.go",
        "sqlite.go",
        "sqlite_test.go",
        "cli_test.go",
        "go.mod",
        "go.sum",
    ],
}
UPLOAD_SHARED = [
    "README.md",
    "CONTRACT.md",
    "STORAGE.md",
    "schema.sql",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "fixtures.json",
    "contract-cases.json",
]
STREAM_FILES = {
    "python": ["app.py", "streaming.py", "test_app.py", "pyproject.toml", "uv.lock"],
    "typescript": [
        "src/app.ts",
        "src/streaming.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    ],
    "go": ["app.go", "streaming.go", "main.go", "app_test.go", "go.mod", "go.sum"],
}
STREAM_SHARED = [
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "fixtures.json",
    "contract-cases.json",
    "client/package.json",
    "client/pnpm-lock.yaml",
    "client/tsconfig.json",
    "client/vite.config.ts",
    "client/index.html",
    "client/src/main.tsx",
    "client/src/style.css",
    "client/src/components/ui/button.tsx",
    "client/src/stream.mjs",
    "client/src/stream.d.mts",
    "client/src/stream.test.mjs",
]
WRITE_FILES = {
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
}
WRITE_SHARED = [
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "fixtures.json",
    "migrations/001.sql",
    "client/package.json",
    "client/pnpm-lock.yaml",
    "client/tsconfig.json",
    "client/vite.config.ts",
    "client/index.html",
    "client/src/main.tsx",
    "client/src/style.css",
    "client/src/components/ui/button.tsx",
    "client/src/protocol.mjs",
    "client/src/protocol.d.mts",
    "client/src/protocol.test.mjs",
    "client/src/fixtures.json",
]
MCP_FILES = {
    "python": [
        "server.py",
        "client.py",
        "protocol.py",
        "test_protocol.py",
        "test_process.py",
        "pyproject.toml",
        "uv.lock",
    ],
}
MCP_SHARED = ["README.md", "CONTRACT.md", "EVIDENCE.md", "AGENTS.md", ".gitignore"]
CHECKPOINT_FILES = {
    "python": [
        "app.py",
        "workflow.py",
        "repository.py",
        "schema.sql",
        "fixtures.json",
        "test_workflow.py",
        "test_repository.py",
        "test_process.py",
        "pyproject.toml",
        "uv.lock",
    ],
}
CHECKPOINT_SHARED = ["README.md", "CONTRACT.md", "EVIDENCE.md", "AGENTS.md", ".gitignore"]
CHUNKING_FILES = {
    "python": [
        "app.py",
        "loader.py",
        "chunking.py",
        "retrieval.py",
        "corpus.json",
        "cases.json",
        "test_loader.py",
        "test_chunking.py",
        "test_retrieval.py",
        "test_cli.py",
        "pyproject.toml",
        "uv.lock",
    ],
}
CHUNKING_SHARED = ["README.md", "CONTRACT.md", "EVIDENCE.md", "AGENTS.md", ".gitignore"]
REGRESSION_FILES = {
    "python": [
        "app.py",
        "loader.py",
        "evaluator.py",
        "corpus.json",
        "cases.json",
        "profiles.json",
        "test_loader.py",
        "test_evaluator.py",
        "test_cli.py",
        "pyproject.toml",
        "uv.lock",
    ],
}
REGRESSION_SHARED = [
    "README.md",
    "CONTRACT.md",
    "MANUAL_EXPECTATIONS.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
]

LABS = {
    "api-contract": (FILES, SHARED),
    "sqlite-storage": (STORAGE_FILES, STORAGE_SHARED),
    "session-authorization": (AUTH_FILES, AUTH_SHARED),
    "text-upload": (UPLOAD_FILES, UPLOAD_SHARED),
    "sse-stream": (STREAM_FILES, STREAM_SHARED),
    "agent-write-safety": (WRITE_FILES, WRITE_SHARED),
    "mcp-readonly": (MCP_FILES, MCP_SHARED),
    "workflow-checkpoint": (CHECKPOINT_FILES, CHECKPOINT_SHARED),
    "document-chunking": (CHUNKING_FILES, CHUNKING_SHARED),
    "output-regression": (REGRESSION_FILES, REGRESSION_SHARED),
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
