"""Download actual standalone course labs through their language-aware lesson UI."""

import json
import os
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[1]
STORAGE_KEY = "deep-ai-station:v1"
LABS = {
    "api-contract": {
        "title": "可运行 API 契约实验",
        "version": "api-contract-v1",
        "lessons": ("fullstack-routing", "fullstack-validation"),
    },
    "sqlite-storage": {
        "title": "可运行 SQLite 数据实验",
        "version": "sqlite-storage-v1",
        "lessons": ("fullstack-database", "fullstack-migrations"),
    },
}
LANGUAGES = [
    ("Python", "python", "example.py"),
    ("TypeScript", "typescript", "example.ts"),
    ("Go", "go", "main.go"),
]
API_FILES = {
    "python": {"app.py", "service.py", "repository.py", "test_app.py", "pyproject.toml", "uv.lock"},
    "typescript": {
        "src/app.ts",
        "src/service.ts",
        "src/repository.ts",
        "src/server.ts",
        "src/app.test.ts",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
    },
    "go": {"app.go", "service.go", "repository.go", "main.go", "app_test.go", "go.mod", "go.sum"},
}
DEPENDENCY_FILES = {
    "python": {"pyproject.toml", "uv.lock"},
    "typescript": {"package.json", "pnpm-lock.yaml", "tsconfig.json"},
    "go": {"go.mod", "go.sum"},
}
COMMON_FILES = {"README.md", "CONTRACT.md", "EVIDENCE.md", "AGENTS.md", ".gitignore"}


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, accept_downloads=True
        )
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()


def goto(page, path):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    page.wait_for_load_state("networkidle")


def lab(page, lab_id):
    return page.get_by_role("region", name=LABS[lab_id]["title"], exact=True)


def stored(page):
    return json.loads(page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY))


def seed_existing_records(page):
    progress = {
        "version": 1,
        "completed": ["agent-agent-loop"],
        "bookmarks": [],
        "notes": {},
        "language": "typescript",
        "runs": [],
        "practice": [
            {
                "lesson_id": "fullstack-integration",
                "language": "go",
                "completed_at": "2026-10-03T00:00:00.000Z",
            }
        ],
        "evidence": [
            {
                "lesson_id": "fullstack-integration",
                "language": "go",
                "revision": "existing-evidence-fixture",
                "command": "go test ./...",
                "success": "已有成功结果",
                "failure": "已有失败结果",
                "pending": "尚未接入真实模型",
                "updated_at": "2026-10-03T00:00:00.000Z",
            }
        ],
    }
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, JSON.stringify(value))",
        [STORAGE_KEY, progress],
    )
    page.reload()
    page.wait_for_load_state("networkidle")


def assert_api_contract_archive(archive, language, files):
    assert API_FILES[language] | {"lessons.json", "contract-cases.json"} <= files
    cases = json.loads(archive.read("contract-cases.json"))
    assert {case["status"] for case in cases} >= {200, 404, 413, 415, 422}
    assert any(case["id"] == "isolated-high-surrogate" for case in cases)
    assert any(case["id"] == "unicode-whitespace" for case in cases)


def assert_standalone_archive(path, lab_id, language, lesson_id):
    source = ROOT / "labs" / lab_id
    with zipfile.ZipFile(path) as archive:
        files = set(archive.namelist())
        assert (
            COMMON_FILES
            | DEPENDENCY_FILES[language]
            | {
                "manifest.json",
                ".prettierrc.json",
            }
            <= files
        )
        assert not any(
            Path(name).is_absolute()
            or Path(name).suffix in {".db", ".sqlite", ".sqlite3"}
            or name.endswith(("-wal", "-shm", "-journal"))
            or any(
                part in {"..", "node_modules", ".venv", "dist", ".git", "__pycache__"}
                or part.startswith(".env")
                for part in Path(name).parts
            )
            for name in files
        )
        for name in files - {"manifest.json", ".prettierrc.json"}:
            shared = source / "shared" / name
            original = shared if shared.is_file() else source / language / name
            assert original.is_file(), f"Archive file has no maintained source: {name}"
            assert archive.read(name) == original.read_bytes()
        assert archive.read(".prettierrc.json") == (ROOT / ".prettierrc.json").read_bytes()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest == json.loads((source / "manifest.json").read_text())
        assert manifest["id"] == lab_id
        assert manifest["version"] == LABS[lab_id]["version"]
        assert lesson_id in manifest["lessons"] and language in manifest["languages"]
        if lab_id == "api-contract":
            assert_api_contract_archive(archive, language, files)
        else:
            assert {"migrations/001.sql", "migrations/002.sql", "contract-cases.json"} <= files
            cases = json.loads(archive.read("contract-cases.json"))
            assert cases["version"] == "sqlite-storage-v1"
            assert {case["id"] for case in cases["cases"]} >= {
                "upgrade-v1-preserves-data",
                "future-schema-refused",
                "audit-failure-rolls-back-entire-write",
                "migration-failure-rolls-back-schema-and-version",
            }
            assert (
                sum("expected" in step for case in cases["cases"] for step in case["steps"]) == 78
            )


@pytest.mark.parametrize(
    ("lab_id", "lesson_id"),
    [(lab_id, lesson) for lab_id, config in LABS.items() for lesson in config["lessons"]],
)
def test_course_lab_downloads_follow_language_and_preserve_reference_bundle(
    page, lab_id, lesson_id
):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    goto(page, f"/lesson/{lesson_id}")
    seed_existing_records(page)
    card = lab(page, lab_id)
    expect(card).to_be_visible()
    expect(card).to_contain_text("完整依赖配置与锁文件、启动入口")
    expect(card).to_contain_text("成功/失败测试")
    if lab_id == "sqlite-storage":
        expect(card).to_contain_text("v1→v2 迁移")
        expect(card).to_contain_text("owner 只是教学输入，不代表登录认证")
    for other in LABS:
        if other != lab_id:
            expect(lab(page, other)).to_have_count(0)
    note = f"{lesson_id}：比较三种语言的同一契约与实际结果"
    page.get_by_label("课程笔记", exact=True).fill(note)
    checkpoint = page.get_by_role("region", name="本课验收", exact=True)
    checkpoint.get_by_role("checkbox").first.check()
    page.get_by_role("radio").first.check()
    before = stored(page)
    page.set_viewport_size({"width": 375, "height": 812})
    for label, language, reference in LANGUAGES:
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.get_by_role("button", name=label, exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        project = card.get_by_role("link", name=f"下载实验 · {label}", exact=True)
        expect(project).to_have_attribute("href", f"/labs/{lab_id}-{language}.zip")
        expect(card.get_by_role("link")).to_have_count(1)
        with page.expect_download() as download:
            project.click()
        assert download.value.suggested_filename == f"{lab_id}-{language}.zip"
        assert_standalone_archive(download.value.path(), lab_id, language, lesson_id)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
        expect(checkpoint.get_by_role("checkbox").first).to_be_checked()
        expect(page.get_by_role("radio").first).to_be_checked()
        old_bundle = page.get_by_role("link", name="下载本课练习资料", exact=True)
        expect(old_bundle).to_have_attribute(
            "href", f"/api/lessons/{lesson_id}/exercise.zip?language={language}"
        )
        with page.expect_download() as reference_download:
            old_bundle.click()
        assert reference_download.value.suggested_filename == f"{lesson_id}-{language}.zip"
        with zipfile.ZipFile(reference_download.value.path()) as archive:
            assert set(archive.namelist()) == {reference, "README.md", "EVIDENCE.md"}
        after = stored(page)
        for field in ("notes", "completed", "practice", "evidence"):
            assert after[field] == before[field]
    page.reload()
    expect(page.get_by_role("button", name="Go", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(card.get_by_role("link", name="下载实验 · Go", exact=True)).to_have_attribute(
        "href", f"/labs/{lab_id}-go.zip"
    )
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for field in ("notes", "completed", "practice", "evidence"):
        assert stored(page)[field] == before[field]
    assert not errors


def test_course_labs_are_limited_to_their_bound_lessons(page):
    for lesson_id in ("fullstack-http", "fullstack-integration", "agent-agent-loop"):
        goto(page, f"/lesson/{lesson_id}")
        for lab_id in LABS:
            expect(lab(page, lab_id)).to_have_count(0)
        expect(page.get_by_role("link", name="下载本课练习资料", exact=True)).to_be_visible()
