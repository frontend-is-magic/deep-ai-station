import io
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

import scripts.check_secrets as scanner
from scripts.check_secrets import audit, scan


def test_sensitive_paths_are_rejected_but_blank_template_is_allowed():
    for name in [
        ".env.local",
        "backend/credentials.json",
        ".aws/config",
        "x/server.pem",
        "x/auth-state.json",
    ]:
        assert "sensitive-path" in audit(name, b"")
    assert (
        audit(
            ".env.example",
            b'DEEPSEEK_API_KEY=\nPLAYGROUND_ACCESS_TOKEN=""\nDEEPSEEK_MODEL=deepseek-flash',
        )
        == []
    )
    assert audit(".env.example", b"DEEPSEEK_API_KEY=placeholder") == ["nonempty-secret-template"]


def test_key_detection_returns_only_rule_ids():
    for payload, rule in [
        (b"sk-" + b"a" * 32, "provider-key"),
        (b"ghp_" + b"b" * 36, "github-token"),
        (b"-----BEGIN " + b"RSA PRIVATE KEY-----", "private-key"),
        (b"AKIA" + b"A" * 16, "aws-access-id"),
    ]:
        assert audit("config.txt", payload) == [rule]
    assert audit("demo.txt", b"test-only; DEEPSEEK_API_KEY; sk-placeholder") == []


def test_zip_contents_are_scanned():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("backend/.env", b"DEEPSEEK_API_KEY=")
        archive.writestr("frontend/main.ts", b"export {}")
    assert scan("starter.zip", buffer.getvalue()) == [
        ("starter.zip!backend/.env", "sensitive-path")
    ]


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "repository"
    root.mkdir()
    monkeypatch.setattr(scanner, "ROOT", root)

    def git(*arguments):
        subprocess.run(  # noqa: S603 - fixed test-owned Git commands in an isolated directory.
            [shutil.which("git") or "/usr/bin/git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    return root, git


@pytest.mark.parametrize("change", ["overwrite", "delete"])
def test_index_credentials_are_found_after_worktree_change(repository, capsys, change):
    root, git = repository
    fake_key = b"sk-" + b"a" * 32
    path = root / "config.txt"
    path.write_bytes(fake_key)
    git("add", "config.txt")
    if change == "overwrite":
        path.write_text("safe working copy")
    else:
        path.unlink()
    with pytest.raises(SystemExit):
        scanner.main()
    output = capsys.readouterr()
    assert "provider-key: index:config.txt" in output.out
    assert "provider-key: worktree:config.txt" not in output.out
    assert fake_key.decode() not in output.out + output.err


def test_index_zip_is_scanned_even_after_worktree_archive_is_cleaned(repository, capsys):
    root, git = repository
    fake_key = b"ghp_" + b"b" * 36
    path = root / "starter.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backend/config.txt", fake_key)
    git("add", "starter.zip")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("backend/config.txt", "safe working copy")
    with pytest.raises(SystemExit):
        scanner.main()
    output = capsys.readouterr()
    assert "github-token: index:starter.zip!backend/config.txt" in output.out
    assert "github-token: worktree:" not in output.out
    assert fake_key.decode() not in output.out + output.err


def test_worktree_changes_untracked_files_and_ignored_tracked_paths_are_checked(repository, capsys):
    root, git = repository
    (root / ".gitignore").write_text(".env.local\n")
    (root / ".env.local").write_text("DEEPSEEK_API_KEY=\n")
    (root / "tracked.txt").write_text("safe index copy")
    git("add", "tracked.txt", ".gitignore")
    git("add", "-f", ".env.local")
    fake_key = b"sk-" + b"c" * 32
    (root / "tracked.txt").write_bytes(fake_key)
    (root / "untracked.txt").write_bytes(fake_key)
    with pytest.raises(SystemExit):
        scanner.main()
    output = capsys.readouterr()
    assert "sensitive-path: index:.env.local" in output.out
    assert "sensitive-path: worktree:.env.local" in output.out
    assert "provider-key: worktree:tracked.txt" in output.out
    assert "provider-key: worktree:untracked.txt" in output.out
    assert "provider-key: index:tracked.txt" not in output.out
    assert fake_key.decode() not in output.out + output.err


def test_blank_template_is_allowed_in_index_and_worktree(repository, capsys):
    root, git = repository
    (root / ".env.example").write_text(
        'DEEPSEEK_API_KEY=\nPLAYGROUND_ACCESS_TOKEN=""\nDEEPSEEK_MODEL=deepseek-flash\n'
    )
    git("add", ".env.example")
    scanner.main()
    assert "1 index blobs, 1 worktree files" in capsys.readouterr().out


def test_symlink_metadata_is_checked_without_reading_its_target(repository, capsys):
    root, git = repository
    target = root.parent / "external.txt"
    target.write_bytes(b"sk-" + b"d" * 32)
    (root / "link.txt").symlink_to(target)
    git("add", "link.txt")
    scanner.main()
    output = capsys.readouterr()
    assert "1 index blobs, 1 worktree files" in output.out
    assert "provider-key" not in output.out


SQLITE_SIDECARS = [
    f"data/progress.{extension}-{suffix}"
    for extension in ("sqlite", "sqlite3", "db")
    for suffix in ("wal", "shm", "journal")
]


@pytest.mark.parametrize(
    "ignore_template",
    [".gitignore", "starters/shared/.gitignore", "labs/api-contract/shared/.gitignore"],
)
def test_sqlite_sidecars_are_ignored_but_force_tracked_copies_are_rejected(
    repository, capsys, ignore_template
):
    root, git = repository
    template = Path(__file__).resolve().parents[1] / ignore_template
    (root / ".gitignore").write_bytes(template.read_bytes())
    (root / "data").mkdir()
    contents = b"test-only SQLite transaction pages"
    for name in SQLITE_SIDECARS:
        (root / name).write_bytes(contents)
        # Check each real path separately: check-ignore with several paths can
        # succeed when only one matched, masking a missing sidecar pattern.
        git("check-ignore", "--", name)
    scanner.main()
    ignored = capsys.readouterr()
    assert "sensitive-path" not in ignored.out
    git("add", ".gitignore")
    git("add", "-f", "--", *SQLITE_SIDECARS)
    with pytest.raises(SystemExit):
        scanner.main()
    output = capsys.readouterr()
    for name in SQLITE_SIDECARS:
        assert f"sensitive-path: index:{name}" in output.out
        assert f"sensitive-path: worktree:{name}" in output.out
    assert contents.decode() not in output.out + output.err


def test_staged_zip_rejects_sqlite_sidecars_after_worktree_archive_is_cleaned(repository, capsys):
    root, git = repository
    archive_path = root / "lesson.zip"
    contents = b"test-only SQLite transaction pages"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name in SQLITE_SIDECARS:
            archive.writestr(name, contents)
        archive.writestr("README.md", "safe lesson guide")
    assert scan("lesson.zip", archive_path.read_bytes()) == [
        (f"lesson.zip!{name}", "sensitive-path") for name in SQLITE_SIDECARS
    ]
    git("add", "lesson.zip")
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "safe lesson guide")
    with pytest.raises(SystemExit):
        scanner.main()
    output = capsys.readouterr()
    for name in SQLITE_SIDECARS:
        assert f"sensitive-path: index:lesson.zip!{name}" in output.out
    assert "sensitive-path: worktree:" not in output.out
    assert contents.decode() not in output.out + output.err
