"""Reject sensitive paths and recognizable credentials without printing their contents."""

import fnmatch
import io
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_NAMES = (
    ".env",
    ".env.*",
    ".envrc",
    ".envrc.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".mcp.json",
    "mcp_config.json",
    "mcp-settings.json",
    "secrets.*",
    "credentials.*",
    "*.secret",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "storage-state*.json",
    "storageState*.json",
    "auth-state*.json",
    "cookies*.json",
    "*.har",
    "*.log",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.sqlite-wal",
    "*.sqlite-shm",
    "*.sqlite-journal",
    "*.sqlite3-wal",
    "*.sqlite3-shm",
    "*.sqlite3-journal",
    "*.db-wal",
    "*.db-shm",
    "*.db-journal",
    "*.tfstate",
    "*.tfstate.*",
)
SENSITIVE_DIRS = {
    "secrets",
    ".secrets",
    "credentials",
    ".credentials",
    ".aws",
    ".ssh",
    ".kube",
    ".azure",
    ".auth",
    ".mcp",
    ".vercel",
    "gcloud",
    "logs",
}
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "provider-key": re.compile(rb"\bsk-(?:proj-|ant-api\d+-)?[A-Za-z0-9_-]{28,}\b"),
    "github-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "aws-access-id": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}
ENV_SECRET = re.compile(
    r"^\s*(?:export\s+)?("
    r"[A-Z0-9_]*(?:API_KEY|TOKEN|PASSWORD|SECRET|PRIVATE_KEY)"
    r"|(?:[A-Z0-9_]*_)?DATABASE_URL)\s*=\s*(.*?)\s*$"
)


def audit(name: str, data: bytes) -> list[str]:
    """Return rule IDs only; callers may display paths, never matched content."""
    path = PurePosixPath(name)
    issues = []
    if any(part in SENSITIVE_DIRS for part in path.parts[:-1]) or (
        path.name != ".env.example"
        and any(fnmatch.fnmatchcase(path.name, pattern) for pattern in SENSITIVE_NAMES)
    ):
        issues.append("sensitive-path")
    for rule, pattern in PATTERNS.items():
        if pattern.search(data):
            issues.append(rule)
    if path.name == ".env.example":
        for line in data.decode("utf-8").splitlines():
            match = ENV_SECRET.match(line)
            if match and match[2] not in ("", "''", '""'):
                issues.append("nonempty-secret-template")
                break
    return issues


def scan(name: str, data: bytes) -> list[tuple[str, str]]:
    issues = [(name, rule) for rule in audit(name, data)]
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    issues.extend(
                        (f"{name}!{member.filename}", rule)
                        for rule in audit(member.filename, archive.read(member))
                    )
    return issues


def git_output(*arguments: str) -> bytes:
    return subprocess.check_output(  # noqa: S603 - internal Git arguments, never shell code.
        [shutil.which("git") or "/usr/bin/git", *arguments], cwd=ROOT
    )


def main():
    issues = []
    index_count = 0
    for entry in git_output("ls-files", "--stage", "-z").split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        mode, blob, _stage = metadata.decode("ascii").split()
        if mode not in {"100644", "100755", "120000"}:
            continue  # Gitlinks and sparse directory entries are not file blobs.
        name = os.fsdecode(raw_name)
        data = git_output("cat-file", "blob", blob)
        found = (
            [(name, rule) for rule in audit(name, data)] if mode == "120000" else scan(name, data)
        )
        index_count += 1
        issues.extend((f"index:{path}", rule) for path, rule in found)
    files = os.fsdecode(
        git_output("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    ).split("\0")
    count = 0
    for name in sorted(set(files) - {""}):
        path = ROOT / name
        if path.is_symlink():
            count += 1
            issues.extend(
                (f"worktree:{name}", rule) for rule in audit(name, os.fsencode(path.readlink()))
            )
        elif path.is_file():
            count += 1
            issues.extend(
                (f"worktree:{member}", rule) for member, rule in scan(name, path.read_bytes())
            )
    for name, rule in issues:
        print(f"{rule}: {name}")
    if issues:
        raise SystemExit("Secret hygiene check failed; matching values were not printed.")
    print(
        f"Secret hygiene: {index_count} index blobs, {count} worktree files and embedded ZIP members checked."
    )


if __name__ == "__main__":
    main()
