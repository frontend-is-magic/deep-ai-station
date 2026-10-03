"""Download the actual standalone course labs through their language-aware lesson UI."""

import json
import os
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[1]
LAB_SOURCE = ROOT / "labs" / "api-contract"
LANGUAGES = [
    (
        "Python",
        "python",
        {"app.py", "service.py", "repository.py", "test_app.py", "pyproject.toml", "uv.lock"},
        "example.py",
    ),
    (
        "TypeScript",
        "typescript",
        {
            "src/app.ts",
            "src/service.ts",
            "src/repository.ts",
            "src/server.ts",
            "src/app.test.ts",
            "package.json",
            "pnpm-lock.yaml",
            "tsconfig.json",
        },
        "example.ts",
    ),
    (
        "Go",
        "go",
        {"app.go", "service.go", "repository.go", "main.go", "app_test.go", "go.mod", "go.sum"},
        "main.go",
    ),
]


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


def lab(page):
    return page.get_by_role("region", name="可运行 API 契约实验", exact=True)


def assert_standalone_archive(path, language, required, lesson_id):
    with zipfile.ZipFile(path) as archive:
        files = set(archive.namelist())
        assert required <= files
        for name in required:
            assert archive.read(name) == (LAB_SOURCE / language / name).read_bytes()
        for name in (
            "README.md",
            "CONTRACT.md",
            "EVIDENCE.md",
            "AGENTS.md",
            ".gitignore",
            "lessons.json",
            "contract-cases.json",
        ):
            assert archive.read(name) == (LAB_SOURCE / "shared" / name).read_bytes()
        assert archive.read(".prettierrc.json") == (ROOT / ".prettierrc.json").read_bytes()
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest == json.loads((LAB_SOURCE / "manifest.json").read_text())
        assert manifest["id"] == "api-contract"
        assert manifest["version"] == "api-contract-v1"
        assert lesson_id in manifest["lessons"] and language in manifest["languages"]
        cases = json.loads(archive.read("contract-cases.json"))
        assert {case["status"] for case in cases} >= {200, 404, 413, 415, 422}
        assert any(case["id"] == "isolated-high-surrogate" for case in cases)
        assert any(case["id"] == "unicode-whitespace" for case in cases)
        assert not any(
            Path(name).is_absolute()
            or any(
                part in {"..", "node_modules", ".venv", "dist", ".git", "__pycache__"}
                or part.startswith(".env")
                for part in Path(name).parts
            )
            for name in files
        )


@pytest.mark.parametrize("lesson_id", ["fullstack-routing", "fullstack-validation"])
def test_course_lab_downloads_follow_language_and_preserve_reference_bundle(page, lesson_id):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    goto(page, f"/lesson/{lesson_id}")
    expect(lab(page)).to_be_visible()
    expect(lab(page)).to_contain_text("完整依赖配置与锁文件、启动入口")
    expect(lab(page)).to_contain_text("成功/失败测试")
    note = f"{lesson_id}：比较同一输入在三个 HTTP 服务中的实际响应"
    page.get_by_label("课程笔记", exact=True).fill(note)
    page.get_by_role("region", name="本课验收", exact=True).get_by_role("checkbox").first.check()
    page.get_by_role("radio").first.check()
    page.set_viewport_size({"width": 375, "height": 812})
    for label, language, required, reference in LANGUAGES:
        page.get_by_role("button", name=label, exact=True).click()
        expect(page.get_by_role("button", name=label, exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        project = lab(page).get_by_role("link", name=f"下载实验 · {label}", exact=True)
        expect(project).to_have_attribute("href", f"/labs/api-contract-{language}.zip")
        expect(lab(page).get_by_role("link")).to_have_count(1)
        with page.expect_download() as download:
            project.click()
        assert download.value.suggested_filename == f"api-contract-{language}.zip"
        assert_standalone_archive(download.value.path(), language, required, lesson_id)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
        expect(
            page.get_by_role("region", name="本课验收", exact=True).get_by_role("checkbox").first
        ).to_be_checked()
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
    page.reload()
    expect(page.get_by_role("button", name="Go", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(lab(page).get_by_role("link", name="下载实验 · Go", exact=True)).to_have_attribute(
        "href", "/labs/api-contract-go.zip"
    )
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors


def test_course_lab_is_limited_to_its_two_lessons(page):
    for lesson_id in ("fullstack-http", "fullstack-integration", "agent-agent-loop"):
        goto(page, f"/lesson/{lesson_id}")
        expect(lab(page)).to_have_count(0)
        expect(page.get_by_role("link", name="下载本课练习资料", exact=True)).to_be_visible()
