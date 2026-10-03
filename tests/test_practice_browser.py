"""Language-specific self-reported practice survives navigation and backup boundaries."""

import copy
import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from backend.curriculum import LESSONS

pytestmark = pytest.mark.e2e

STORAGE_KEY = "deep-ai-station:v1"
NOTE = "已比较固定成功与失败输入，保留这条原有课程笔记。"


@pytest.fixture
def page():
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, accept_downloads=True
        )
        current = context.new_page()
        current.on("pageerror", lambda error: errors.append(str(error)))
        try:
            yield current
        finally:
            context.close()
            browser.close()
            assert not errors, errors


def goto(page, path):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    page.wait_for_load_state("networkidle")


def practice(page):
    return page.get_by_role("region", name="本课实践记录", exact=True)


def confirmation(page, language):
    return practice(page).get_by_role(
        "checkbox", name=f"我已用{language}运行成功与失败样例，并记录实际结果", exact=True
    )


def mark(page, language):
    confirmation(page, language).check()
    practice(page).get_by_role("button", name=f"标记 {language} 实践完成", exact=True).click()
    expect(
        practice(page).get_by_role("button", name=f"撤销 {language} 实践记录", exact=True)
    ).to_be_visible()


def stored(page):
    value = page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY)
    return json.loads(value) if value else {}


def pairs(page):
    return {(item["lesson_id"], item["language"]) for item in stored(page).get("practice", [])}


def preferences(page):
    settings = page.get_by_role("button", name="学习偏好与数据", exact=True)
    if not settings.is_visible():
        page.get_by_role("button", name="打开导航", exact=True).click()
    settings.click()
    return page.get_by_role("dialog", name="你的学习空间", exact=True)


def close_preferences(page):
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog", name="你的学习空间", exact=True)).not_to_be_visible()
    expect(page.get_by_role("button", name="关闭导航", exact=True)).not_to_be_visible()


def upload(dialog, data):
    dialog.locator('input[type="file"]').set_input_files(
        {
            "name": "language-practice-progress.json",
            "mimeType": "application/json",
            "buffer": json.dumps(data, ensure_ascii=False).encode(),
        }
    )


def expect_route_counts(page, counts):
    region = page.get_by_role("region", name="路线实践记录", exact=True)
    for language, count in counts.items():
        expect(region.get_by_role("listitem").filter(has_text=language)).to_contain_text(
            f"{count}/24 节已记录实践"
        )
    expect(page.locator(".overview-progress")).to_contain_text("0/24 已完成")
    expect(page.locator(".overview-progress strong")).to_have_text("0%")


def test_legacy_v1_completion_does_not_invent_language_practice(page):
    legacy = {
        "version": 1,
        "completed": ["fullstack-routing"],
        "bookmarks": [],
        "notes": {"fullstack-routing": NOTE},
        "language": "go",
        "runs": [],
    }
    goto(page, "/lesson/fullstack-routing")
    page.evaluate(
        "([key, value]) => localStorage.setItem(key, JSON.stringify(value))",
        [STORAGE_KEY, legacy],
    )
    page.reload()
    expect(page.get_by_role("button", name="取消完成标记", exact=True)).to_be_visible()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(NOTE)
    for language in ("Go", "Python", "TypeScript"):
        page.get_by_role("button", name=language, exact=True).click()
        expect(practice(page).get_by_text(f"{language} · 未记录", exact=True)).to_be_visible()
        expect(confirmation(page, language)).not_to_be_checked()
        expect(
            practice(page).get_by_role("button", name=f"标记 {language} 实践完成", exact=True)
        ).to_be_disabled()
    assert not stored(page).get("practice")
    assert stored(page)["completed"] == legacy["completed"]
    assert stored(page)["notes"] == legacy["notes"]


def test_practice_isolates_languages_lessons_and_revocation_with_route_counts(page):
    goto(page, "/lesson/fullstack-routing")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_label("课程笔记", exact=True).fill(NOTE)
    checkpoint = page.get_by_role("region", name="本课验收", exact=True)
    checkpoint.get_by_role("checkbox").first.check()
    page.get_by_role("radio").first.check()
    confirmation(page, "Go").check()
    page.get_by_role("button", name="Python", exact=True).click()
    expect(confirmation(page, "Python")).not_to_be_checked()
    page.get_by_role("button", name="Go", exact=True).click()
    expect(confirmation(page, "Go")).not_to_be_checked()
    mark(page, "Go")
    expect(checkpoint.get_by_role("checkbox").first).to_be_checked()
    expect(page.get_by_role("radio").first).to_be_checked()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(NOTE)
    first = stored(page)["practice"][0]
    assert set(first) == {"lesson_id", "language", "completed_at"}
    assert datetime.fromisoformat(first["completed_at"].replace("Z", "+00:00")).tzinfo
    expect(practice(page).locator("time")).to_have_attribute("datetime", first["completed_at"])
    assert pairs(page) == {("fullstack-routing", "go")}
    assert stored(page)["completed"] == []

    goto(page, "/roadmap/fullstack")
    expect_route_counts(page, {"Go": 1, "Python": 0, "TypeScript": 0})
    goto(page, "/lesson/fullstack-routing")
    page.get_by_role("button", name="TypeScript", exact=True).click()
    confirmation(page, "TypeScript").check()
    # In-app navigation reuses the Lesson route; the unchecked state must follow its key.
    page.locator('.lesson-pagination a[href="/lesson/fullstack-async"]').click()
    expect(confirmation(page, "TypeScript")).not_to_be_checked()
    for language in ("Go", "Python", "TypeScript"):
        expect(practice(page).get_by_text(f"{language} · 未记录", exact=True)).to_be_visible()
    assert pairs(page) == {("fullstack-routing", "go")}
    page.locator('.lesson-pagination a[href="/lesson/fullstack-routing"]').click()
    expect(confirmation(page, "TypeScript")).not_to_be_checked()
    page.get_by_role("button", name="Go", exact=True).click()
    page.reload()
    expect(
        practice(page).get_by_role("button", name="撤销 Go 实践记录", exact=True)
    ).to_be_visible()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(NOTE)
    assert stored(page)["practice"][0]["completed_at"] == first["completed_at"]

    page.get_by_role("button", name="Python", exact=True).click()
    mark(page, "Python")
    python_record = next(item for item in stored(page)["practice"] if item["language"] == "python")
    page.get_by_role("button", name="Go", exact=True).click()
    practice(page).get_by_role("button", name="撤销 Go 实践记录", exact=True).click()
    expect(confirmation(page, "Go")).not_to_be_checked()
    assert stored(page)["practice"] == [python_record]
    assert stored(page)["completed"] == []
    assert stored(page)["notes"] == {"fullstack-routing": NOTE}
    goto(page, "/roadmap/fullstack")
    expect_route_counts(page, {"Go": 0, "Python": 1, "TypeScript": 0})
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_agent_practice_is_python_only_without_changing_fullstack_preference(page):
    goto(page, "/lesson/fullstack-routing")
    page.get_by_role("button", name="Go", exact=True).click()
    goto(page, "/lesson/agent-agent-loop")
    expect(practice(page).get_by_role("listitem")).to_have_count(1)
    expect(practice(page).get_by_text("Python · 未记录", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Go", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="TypeScript", exact=True)).to_have_count(0)
    mark(page, "Python")
    assert pairs(page) == {("agent-agent-loop", "python")}
    assert stored(page)["language"] == "go"
    assert stored(page)["completed"] == []
    page.set_viewport_size({"width": 375, "height": 812})
    expect(practice(page)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.reload()
    expect(
        practice(page).get_by_role("button", name="撤销 Python 实践记录", exact=True)
    ).to_be_visible()
    assert stored(page)["language"] == "go"


def test_practice_backup_round_trip_in_fresh_context_rejects_duplicate_records(page):
    goto(page, "/lesson/fullstack-integration")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_label("课程笔记", exact=True).fill(NOTE)
    page.get_by_role("region", name="毕业实践证据", exact=True).get_by_label(
        "代码版本 / commit", exact=True
    ).fill("practice-backup-fixture")
    mark(page, "Go")
    page.get_by_role("button", name="Python", exact=True).click()
    mark(page, "Python")
    dialog = preferences(page)
    with page.expect_download() as download:
        dialog.get_by_role("button", name="导出学习记录", exact=True).click()
    assert download.value.suggested_filename == "deep-ai-station-progress.json"
    backup = json.loads(Path(download.value.path()).read_text())
    assert len(backup["practice"]) == 2
    assert backup["evidence"][0]["revision"] == "practice-backup-fixture"
    assert backup["completed"] == []

    errors = []
    fresh = page.context.browser.new_context(
        viewport={"width": 375, "height": 812}, accept_downloads=True
    )
    try:
        restored = fresh.new_page()
        restored.on("pageerror", lambda error: errors.append(str(error)))
        goto(restored, "/lesson/fullstack-integration")
        assert not stored(restored).get("practice")
        expect(restored.get_by_label("课程笔记", exact=True)).to_have_value("")
        dialog = preferences(restored)
        upload(dialog, backup)
        expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
        close_preferences(restored)
        expect(
            practice(restored).get_by_role("button", name="撤销 Python 实践记录", exact=True)
        ).to_be_visible()
        assert stored(restored) == backup
        expect(restored.get_by_label("课程笔记", exact=True)).to_have_value(NOTE)
        restored.get_by_role("button", name="Go", exact=True).click()
        expect(
            restored.get_by_role("region", name="毕业实践证据", exact=True).get_by_label(
                "代码版本 / commit", exact=True
            )
        ).to_have_value("practice-backup-fixture")
        unchanged = stored(restored)
        invalid = copy.deepcopy(backup)
        invalid["practice"].append(copy.deepcopy(invalid["practice"][0]))
        dialog = preferences(restored)
        upload(dialog, invalid)
        expect(dialog.get_by_role("status")).to_have_text("学习记录格式不正确")
        assert stored(restored) == unchanged
        close_preferences(restored)
        expect(
            practice(restored).get_by_role("button", name="撤销 Go 实践记录", exact=True)
        ).to_be_visible()
        assert restored.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
    finally:
        fresh.close()


def test_download_evidence_and_common_completion_do_not_mark_language_practice(page):
    lesson_id = "fullstack-integration"
    goto(page, f"/lesson/{lesson_id}")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_label("课程笔记", exact=True).fill(NOTE)
    evidence = page.get_by_role("region", name="毕业实践证据", exact=True)
    evidence.get_by_label("代码版本 / commit", exact=True).fill("common-completion-fixture")
    assert not stored(page).get("practice")
    with page.expect_download() as download:
        page.get_by_role("link", name="下载完整项目骨架 · Go", exact=True).click()
    assert download.value.suggested_filename == "fullstack-go.zip"
    assert Path(download.value.path()).stat().st_size > 0
    assert not stored(page).get("practice")
    page.get_by_role("radio").nth(LESSONS[lesson_id]["quiz"]["answer"]).check()
    checkpoint = page.get_by_role("region", name="本课验收", exact=True)
    for checkbox in checkpoint.get_by_role("checkbox").all():
        checkbox.check()
    expect(checkpoint.get_by_role("button", name="标记本课完成", exact=True)).to_be_disabled()
    page.get_by_role("button", name="检查答案", exact=True).click()
    checkpoint.get_by_role("button", name="标记本课完成", exact=True).click()
    assert stored(page)["completed"] == [lesson_id]
    assert not stored(page).get("practice")
    expect(confirmation(page, "Go")).not_to_be_checked()
    expect(
        practice(page).get_by_role("button", name="标记 Go 实践完成", exact=True)
    ).to_be_disabled()
    page.reload()
    expect(page.get_by_role("button", name="取消完成标记", exact=True)).to_be_visible()
    expect(evidence.get_by_label("代码版本 / commit", exact=True)).to_have_value(
        "common-completion-fixture"
    )
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(NOTE)
    assert not stored(page).get("practice")
    page.set_viewport_size({"width": 375, "height": 812})
    expect(practice(page)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
