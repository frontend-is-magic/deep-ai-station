"""Three backend downloads share one client without changing learner record keys.

The runner owns services. These platform flows inspect actual downloaded bytes;
the independent ZIP verifier owns execution and navigation inside the client.
"""

import hashlib
import json
import os
import zipfile
from pathlib import Path

import pytest
from playwright.sync_api import expect
from test_evidence_library_browser import evidence
from test_lab_evidence_browser import FIELDS, expect_fields
from test_lab_evidence_browser import card as evidence_card
from test_practice_browser import confirmation, practice
from test_progress_write_browser import EPOCH_A, base_url
from test_progress_write_browser import page as page
from test_run_history_browser import progress, put_progress, record, stored

from backend.curriculum import LESSONS

pytestmark = pytest.mark.e2e
TITLE = "可运行 API 契约实验"
LESSON_IDS = ("fullstack-routing", "fullstack-validation")
LANGUAGES = (("python", "Python"), ("typescript", "TypeScript"), ("go", "Go"))
TIME = "2026-10-04T12:00:00.000Z"
CLIENT_FILES = {
    "client/package.json",
    "client/pnpm-lock.yaml",
    "client/tsconfig.json",
    "client/vite.config.ts",
    "client/index.html",
    "client/src/main.tsx",
    "client/src/App.tsx",
    "client/src/style.css",
    "client/src/components/ui/button.tsx",
    "client/src/navigation.ts",
    "client/src/navigation.test.ts",
    "client/src/protocol.ts",
    "client/src/protocol.test.ts",
    "client/src/controller.ts",
    "client/src/controller.test.ts",
}
SHARED_FILES = {
    "README.md",
    "CONTRACT.md",
    "CLIENT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "lessons.json",
    "contract-cases.json",
    "manifest.json",
    ".prettierrc.json",
}
BACKEND_FILES = {
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
ASSET_DIGESTS = {
    "lessons.json": "1668bc4b74b9645c0df5507056e632112187ff1885721d0f6b1d9bc2deea3e2a",
    "contract-cases.json": "5cb511cccd25a3459aab1e21a22f603ec3b46edab81b8d5a441e93c93a7774d3",
}


def goto(page, path):
    target = base_url() + path
    page.goto(target)
    expect(page).to_have_url(target)
    page.wait_for_load_state("networkidle")


def lesson(page, lesson_id, language=None):
    suffix = f"?language={language}" if language else ""
    goto(page, f"/lesson/{lesson_id}{suffix}")
    expect(
        page.get_by_role("heading", name=LESSONS[lesson_id]["title"], exact=True)
    ).to_be_visible()
    return page.get_by_role("region", name=TITLE, exact=True)


def original_progress(records):
    return progress(
        history_reset_id=EPOCH_A,
        language="go",
        completed=["fullstack-routing", "fullstack-http"],
        notes={lesson_id: f"保留 {lesson_id} 原笔记" for lesson_id in LESSON_IDS},
        bookmarks=["hono-guide"],
        runs=[record(1)],
        practice=[
            {"lesson_id": "fullstack-routing", "language": "go", "completed_at": TIME},
            {"lesson_id": "fullstack-validation", "language": "python", "completed_at": TIME},
        ],
        evidence=records,
    )


def seed(page, value):
    goto(page, "/")
    put_progress(page, value)


def unchanged_except(before, after, *fields):
    # Visiting a course intentionally updates the independent resume position.
    excluded = {"resume", *fields}
    assert {key: value for key, value in after.items() if key not in excluded} == {
        key: value for key, value in before.items() if key not in excluded
    }


def archive_client(download, language):
    assert download.failure() is None
    assert download.suggested_filename == f"api-contract-{language}.zip"
    with zipfile.ZipFile(Path(download.path())) as archive:
        names = archive.namelist()
        expected = CLIENT_FILES | SHARED_FILES | BACKEND_FILES[language]
        assert len(names) == {"python": 31, "typescript": 33, "go": 32}[language]
        assert len(names) == len(set(names)) and set(names) == expected
        assert archive.testzip() is None
        assert json.loads(archive.read("manifest.json")) == {
            "id": "api-contract",
            "version": "api-contract-v1",
            "lessons": list(LESSON_IDS),
            "languages": ["python", "typescript", "go"],
        }
        assert "api-navigation-v1" in archive.read("CLIENT.md").decode("utf-8")
        for name, digest in ASSET_DIGESTS.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        assert all(archive.read(name).strip() for name in BACKEND_FILES[language])
        package = json.loads(archive.read("client/package.json"))
        assert {"react", "jotai", "react-router-dom"} <= package["dependencies"].keys()
        assert package["scripts"]["check"]
        return {name: archive.read(name) for name in sorted(CLIENT_FILES)}


def test_three_real_backend_packages_share_client_and_keep_language_specific_progress(page):
    original = original_progress([evidence("fullstack-routing", "go", command="旧 Go 证据")])
    seed(page, original)
    clients = []
    for language, label in LANGUAGES:
        lab = lesson(page, "fullstack-routing", language)
        expect(page.get_by_role("button", name=label, exact=True)).to_have_attribute(
            "aria-pressed", "true"
        )
        expect(practice(page)).to_contain_text("当前实践：" + label)
        expect(evidence_card(page)).to_contain_text("当前记录：" + label)
        expect(page.get_by_role("region", name="公共前端实验实践记录", exact=True)).to_have_count(0)
        link = lab.get_by_role("link", name=f"下载实验 · {label}", exact=True)
        expect(link).to_have_attribute("href", f"/labs/api-contract-{language}.zip")
        before = stored(page)
        with page.expect_download() as event:
            link.click()
        clients.append(archive_client(event.value, language))
        assert stored(page) == before
        assert stored(page)["language"] == language
        unchanged_except(original, stored(page), "language")
    assert clients[0] == clients[1] == clients[2]


def test_existing_six_evidence_keys_remain_editable_at_capacity_and_reference_zip_is_unchanged(
    page,
):
    six = [
        evidence(
            lesson_id,
            language,
            revision=f"原版本 {lesson_id}/{language}",
            command=f"原验证命令 {lesson_id}/{language}",
            pending="新客户端尚未运行；旧记录不构成新增功能已验收的证据。",
        )
        for lesson_id in LESSON_IDS
        for language, _ in LANGUAGES
    ]
    original = original_progress(
        six + [evidence(f"fullstack-legacy-api-{index}") for index in range(42)]
    )
    seed(page, original)
    for lesson_id in LESSON_IDS:
        lesson(page, lesson_id)
        expect(evidence_card(page)).to_contain_text("当前记录：Go")
        expect_fields(
            page,
            next(
                item for item in six if item["lesson_id"] == lesson_id and item["language"] == "go"
            ),
        )
        expect(page.get_by_label("课程笔记", exact=True)).to_have_value(
            original["notes"][lesson_id]
        )
        if lesson_id == "fullstack-routing":
            expect(page.get_by_role("button", name="取消完成标记", exact=True)).to_be_visible()
            expect(
                practice(page).get_by_role("button", name="撤销 Go 实践记录", exact=True)
            ).to_be_visible()
        else:
            expect(confirmation(page, "Go")).not_to_be_checked()
            expect(
                practice(page).get_by_role("button", name="标记 Go 实践完成", exact=True)
            ).to_be_disabled()
    unchanged_except(original, stored(page))
    changed = "学习者补充：公开检索、详情、返回、刷新；平台没有代我运行独立客户端。"
    field = evidence_card(page).get_by_label(FIELDS["success"], exact=True)
    expect(field).to_be_enabled()
    field.fill(changed)
    after = stored(page)
    unchanged_except(original, after, "evidence")
    assert len(after["evidence"]) == 48
    for before_record, after_record in zip(original["evidence"], after["evidence"], strict=True):
        if (
            before_record["lesson_id"] == "fullstack-validation"
            and before_record["language"] == "go"
        ):
            assert after_record["success"] == changed
            assert {
                key: value
                for key, value in after_record.items()
                if key not in {"success", "updated_at"}
            } == {
                key: value
                for key, value in before_record.items()
                if key not in {"success", "updated_at"}
            }
        else:
            assert after_record == before_record
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(field).to_have_value(changed)
    unchanged_except(after, stored(page))
    page.set_viewport_size({"width": 375, "height": 812})
    lab = page.get_by_role("region", name=TITLE, exact=True)
    lab.scroll_into_view_if_needed()
    expect(lab).to_be_in_viewport()
    expect(lab.get_by_role("link", name="下载实验 · Go", exact=True)).to_have_attribute(
        "href", "/labs/api-contract-go.zip"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    reference = page.get_by_role("link", name="下载本课练习资料", exact=True)
    expect(reference).to_have_attribute(
        "href", "/api/lessons/fullstack-validation/exercise.zip?language=go"
    )
    with page.expect_download() as event:
        reference.focus()
        expect(reference).to_be_focused()
        page.keyboard.press("Enter")
    assert event.value.failure() is None
    assert event.value.suggested_filename == "fullstack-validation-go.zip"
    with zipfile.ZipFile(Path(event.value.path())) as archive:
        assert len(archive.namelist()) == 3
        assert set(archive.namelist()) == {"main.go", "README.md", "EVIDENCE.md"}
        assert archive.testzip() is None
        assert (
            archive.read("main.go").decode("utf-8")
            == LESSONS["fullstack-validation"]["snippets"]["go"]
        )
    directory = os.getenv("E2E_SCREENSHOT_DIR")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        lab.scroll_into_view_if_needed()
        page.screenshot(path=str(target / "deep-ai-api-navigation-course-mobile.png"))
    back = page.get_by_role("link", name="返回 AI 全栈工程", exact=True)
    back.scroll_into_view_if_needed()
    expect(back).to_be_in_viewport()
    back.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/roadmap/fullstack")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert stored(page)["language"] == "go"
    unchanged_except(after, stored(page))
