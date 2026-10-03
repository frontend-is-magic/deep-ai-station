"""Learning positions restore real visits without claiming course completion."""

import copy
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from backend.curriculum import LESSONS, TRACKS

pytestmark = pytest.mark.e2e
STORAGE_KEY = "deep-ai-station:v1"
TIMESTAMP = "2026-10-04T01:02:03.000Z"


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
    expect(page.get_by_role("main")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_be_visible()


def stored(page):
    value = page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY)
    return json.loads(value) if value else {}


def preferences(page):
    settings = page.get_by_role("button", name="学习偏好与数据", exact=True)
    if not settings.is_visible():
        page.get_by_role("button", name="打开导航", exact=True).click()
    settings.click()
    return page.get_by_role("dialog", name="你的学习空间", exact=True)


def close_preferences(page):
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog", name="你的学习空间", exact=True)).not_to_be_visible()


def import_progress(page, data, accepted=True):
    dialog = preferences(page)
    dialog.locator('input[type="file"]').set_input_files(
        {
            "name": "resume-progress.json",
            "mimeType": "application/json",
            "buffer": json.dumps(data, ensure_ascii=False).encode(),
        }
    )
    expect(dialog.get_by_role("status")).to_have_text(
        "学习记录已导入" if accepted else "学习记录格式不正确"
    )
    close_preferences(page)


def legacy_progress():
    return {
        "version": 1,
        "completed": [],
        "bookmarks": [],
        "notes": {},
        "language": "go",
        "runs": [],
    }


def resume_record(track, lesson_id):
    return {
        "last_track": track,
        "positions": {track: {"lesson_id": lesson_id, "visited_at": TIMESTAMP}},
    }


def expect_resume(page, lesson_id, completed=False):
    region = page.get_by_role("region", name="上次学习", exact=True)
    expect(region).to_contain_text(LESSONS[lesson_id]["title"])
    expect(
        region.get_by_role("link", name="回顾本课" if completed else "继续本课", exact=True)
    ).to_have_attribute("href", f"/lesson/{lesson_id}")
    return region


def test_real_visits_restore_last_route_with_equal_clocks_and_refresh(page):
    page.clock.set_fixed_time(datetime(2026, 10, 4, 1, 2, 3, tzinfo=UTC))
    goto(page, "/lesson/fullstack-routing")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_label("课程笔记", exact=True).fill("保留实际访问前的学习记录")
    checkpoint = page.get_by_role("region", name="本课验收", exact=True)
    checkpoint.get_by_role("checkbox").first.check()
    page.get_by_role("radio").first.check()
    position = stored(page)["resume"]
    # Editing notes, changing language and local quiz choices must not count as new visits.
    assert position["last_track"] == "fullstack"
    page.get_by_label("课程笔记", exact=True).fill("笔记与访问位置独立")
    assert stored(page)["resume"] == position
    page.locator('.lesson-pagination a[href="/lesson/fullstack-async"]').click()
    expect(page.get_by_role("heading", level=1)).to_have_text(LESSONS["fullstack-async"]["title"])
    page.wait_for_function(
        "key => JSON.parse(localStorage.getItem(key)).resume.positions.fullstack.lesson_id === 'fullstack-async'",
        arg=STORAGE_KEY,
    )
    page.locator('.lesson-pagination a[href="/lesson/fullstack-routing"]').click()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("笔记与访问位置独立")
    page.wait_for_function(
        "key => JSON.parse(localStorage.getItem(key)).resume.positions.fullstack.lesson_id === 'fullstack-routing'",
        arg=STORAGE_KEY,
    )
    goto(page, "/")
    expect_resume(page, "fullstack-routing")
    first_fullstack = next(track for track in TRACKS if track["id"] == "fullstack")["lessons"][0]
    expect(page.get_by_role("region", name="下一节未完成课", exact=True)).to_contain_text(
        first_fullstack["title"]
    )
    goto(page, "/lesson/agent-model-context")
    goto(page, "/")
    expect_resume(page, "agent-model-context")
    state = stored(page)
    assert state["resume"]["last_track"] == "agent"
    assert {record["visited_at"] for record in state["resume"]["positions"].values()} == {TIMESTAMP}
    assert state["language"] == "go"
    assert state["completed"] == []
    assert not state.get("practice")
    assert state["notes"] == {"fullstack-routing": "笔记与访问位置独立"}
    page.reload()
    expect_resume(page, "agent-model-context")
    goto(page, "/roadmap/fullstack")
    expect_resume(page, "fullstack-routing").get_by_role(
        "link", name="继续本课", exact=True
    ).click()
    expect(page).to_have_url(re.compile(r"/lesson/fullstack-routing$"))
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("笔记与访问位置独立")
    expect(page.get_by_role("button", name="Go", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    goto(page, "/")
    expect_resume(page, "fullstack-routing")
    goto(page, "/roadmap/agent")
    expect_resume(page, "agent-model-context")
    assert stored(page)["resume"]["last_track"] == "fullstack"


def test_legacy_backup_visits_preserve_progress_and_invalid_lessons_do_not_record(page):
    backup = legacy_progress()
    backup["completed"] = ["agent-agent-loop"]
    backup["notes"] = {"fullstack-integration": "原有笔记"}
    backup["practice"] = [
        {"lesson_id": "fullstack-integration", "language": "go", "completed_at": TIMESTAMP}
    ]
    backup["evidence"] = [
        {
            "lesson_id": "fullstack-integration",
            "language": "go",
            "revision": "example-commit",
            "command": "pnpm check",
            "success": "已通过",
            "failure": "已验证失败",
            "pending": "生产未验证",
            "updated_at": TIMESTAMP,
        }
    ]
    goto(page, "/")
    import_progress(page, backup)
    assert stored(page) == backup
    expect(page.get_by_role("region", name="上次学习", exact=True)).to_have_count(0)
    goto(page, "/lesson/fullstack-integration")
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("原有笔记")
    page.wait_for_function(
        "key => Boolean(JSON.parse(localStorage.getItem(key)).resume)", arg=STORAGE_KEY
    )
    assert {key: value for key, value in stored(page).items() if key != "resume"} == backup
    before = stored(page)
    goto(page, "/lesson/fullstack-not-a-real-lesson")
    expect(page.get_by_role("heading", name="课时不存在", exact=True)).to_be_visible()
    assert stored(page) == before
    goto(page, "/")
    expect_resume(page, "fullstack-integration")
    # A replacement old backup clears the new optional location without changing old fields.
    import_progress(page, backup)
    page.reload()
    expect(page.get_by_role("region", name="上次学习", exact=True)).to_have_count(0)
    assert stored(page) == backup


def test_positions_export_import_and_invalid_backup_does_not_replace_current_state(page):
    goto(page, "/lesson/fullstack-validation")
    goto(page, "/lesson/agent-agent-loop")
    goto(page, "/")
    dialog = preferences(page)
    with page.expect_download() as download:
        dialog.get_by_role("button", name="导出学习记录", exact=True).click()
    backup = json.loads(Path(download.value.path()).read_text())
    assert backup["resume"]["last_track"] == "agent"
    close_preferences(page)
    errors = []
    context = page.context.browser.new_context(viewport={"width": 375, "height": 812})
    try:
        restored = context.new_page()
        restored.on("pageerror", lambda error: errors.append(str(error)))
        goto(restored, "/")
        import_progress(restored, backup)
        expect_resume(restored, "agent-agent-loop")
        restored.reload()
        expect_resume(restored, "agent-agent-loop")
        assert stored(restored) == backup
        invalid = copy.deepcopy(backup)
        invalid["resume"]["positions"]["agent"]["visited_at"] = "2026-02-30T00:00:00.000Z"
        import_progress(restored, invalid, accepted=False)
        assert stored(restored) == backup
        expect_resume(restored, "agent-agent-loop")
        assert restored.evaluate("document.documentElement.scrollWidth <= innerWidth")
        goto(restored, "/roadmap/fullstack")
        expect_resume(restored, "fullstack-validation")
        assert restored.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert not errors
    finally:
        context.close()


def test_stale_and_mismatched_positions_fall_back_without_losing_notes(page):
    backup = legacy_progress()
    backup["notes"] = {"agent-agent-loop": "记录保留"}
    backup["resume"] = resume_record("agent", "agent-agent-loop")
    backup["resume"]["last_track"] = "fullstack"
    backup["resume"]["positions"]["fullstack"] = {
        "lesson_id": "fullstack-removed",
        "visited_at": TIMESTAMP,
    }
    goto(page, "/")
    import_progress(page, backup)
    expect_resume(page, "agent-agent-loop")
    goto(page, "/roadmap/fullstack")
    region = page.get_by_role("region", name="上次学习", exact=True)
    expect(region.get_by_role("link")).to_have_count(0)
    expect(
        page.get_by_role("region", name="下一节未完成课", exact=True).get_by_role(
            "link", name="进入课程", exact=True
        )
    ).to_be_visible()
    backup["resume"]["positions"]["fullstack"]["lesson_id"] = "agent-agent-loop"
    import_progress(page, backup)
    expect(region.get_by_role("link")).to_have_count(0)
    goto(page, "/")
    expect_resume(page, "agent-agent-loop")
    assert stored(page) == backup


def test_completed_routes_offer_review_without_inventing_a_next_lesson(page):
    backup = legacy_progress()
    backup["completed"] = list(LESSONS)
    backup["resume"] = resume_record("fullstack", "fullstack-routing")
    goto(page, "/")
    import_progress(page, backup)
    expect_resume(page, "fullstack-routing", completed=True)
    next_region = page.get_by_role("region", name="下一节未完成课", exact=True)
    expect(next_region).to_contain_text("课程已完成")
    expect(next_region.get_by_role("link")).to_have_count(0)
    expect(page.get_by_role("link", name="继续本课", exact=True)).to_have_count(0)
    goto(page, "/roadmap/fullstack")
    expect_resume(page, "fullstack-routing", completed=True)
    expect(page.get_by_role("region", name="下一节未完成课", exact=True)).to_contain_text(
        "本路线课程已完成"
    )
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    goto(page, "/roadmap/agent")
    expect(
        page.get_by_role("region", name="下一节未完成课", exact=True).get_by_role("link")
    ).to_have_count(0)
    expect(page.get_by_role("link", name="开始第一课", exact=True)).to_have_count(0)
    del backup["resume"]
    goto(page, "/")
    import_progress(page, backup)
    expect(page.get_by_role("link", name="查看学习路线", exact=True)).to_be_visible()
    expect(
        page.get_by_role("region", name="下一节未完成课", exact=True).get_by_role("link")
    ).to_have_count(0)
