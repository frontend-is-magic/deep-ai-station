import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e

STORAGE_KEY = "deep-ai-station:v1"
FIELD_LABELS = {
    "revision": "代码版本 / commit",
    "command": "验证命令",
    "success": "成功输入与实际结果",
    "failure": "失败输入与实际结果",
    "pending": "未验证事项",
}
GO_EVIDENCE = {
    "revision": "fixture-go-20261003",
    "command": "go test -mod=readonly ./...\npnpm check",
    "success": "输入 API 契约问题；实际返回 200 与固定来源。",
    "failure": "输入空问题；实际返回 422，界面保留可恢复提示。",
    "pending": "真实 DeepSeek 请求与生产发布尚未验证。",
}


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()


def goto(page, path):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    page.wait_for_load_state("networkidle")


def card(page):
    return page.get_by_role("region", name="毕业实践证据", exact=True)


def fill_evidence(page, values):
    for field, value in values.items():
        card(page).get_by_label(FIELD_LABELS[field], exact=True).fill(value)


def expect_evidence(page, values):
    for field, label in FIELD_LABELS.items():
        expect(card(page).get_by_label(label, exact=True)).to_have_value(values.get(field, ""))


def stored(page):
    return json.loads(page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY))


def upload_backup(page, backup):
    page.locator('input[type="file"]').set_input_files(
        {
            "name": "evidence-progress.json",
            "mimeType": "application/json",
            "buffer": json.dumps(backup, ensure_ascii=False).encode(),
        }
    )
    expect(page.get_by_role("status")).to_contain_text("学习记录已导入")


def test_evidence_survives_refresh_and_isolates_course_and_language(page):
    goto(page, "/lesson/fullstack-integration")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_label("课程笔记", exact=True).fill("已有课程笔记应保留")
    before = stored(page)
    expect(card(page)).to_be_visible()
    expect(
        card(page).get_by_role("button", name="下载实践证据 Markdown", exact=True)
    ).to_be_disabled()
    fill_evidence(page, GO_EVIDENCE)
    page.reload()
    expect_evidence(page, GO_EVIDENCE)
    page.get_by_role("button", name="Python", exact=True).click()
    expect_evidence(page, {})
    fill_evidence(page, {"revision": "fixture-python-independent"})
    page.get_by_role("button", name="Go", exact=True).click()
    expect_evidence(page, GO_EVIDENCE)
    goto(page, "/lesson/fullstack-launch")
    expect_evidence(page, {})
    fill_evidence(page, {"pending": "上线课独立待验证项"})
    goto(page, "/lesson/fullstack-integration")
    expect_evidence(page, GO_EVIDENCE)
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("已有课程笔记应保留")
    after = stored(page)
    assert after["notes"] == before["notes"]
    assert after["completed"] == before["completed"] == []
    assert len(after["evidence"]) == 3
    go_record = next(
        record
        for record in after["evidence"]
        if record["lesson_id"] == "fullstack-integration" and record["language"] == "go"
    )
    assert all(go_record[field] == value for field, value in GO_EVIDENCE.items())
    with page.expect_download() as download:
        card(page).get_by_role("button", name="下载实践证据 Markdown", exact=True).click()
    assert download.value.suggested_filename == "fullstack-integration-go-evidence.md"
    markdown = Path(download.value.path()).read_text()
    assert "fullstack-integration" in markdown
    assert "Go" in markdown
    assert all(value in markdown for value in GO_EVIDENCE.values())
    assert "未经平台核验" in markdown and "不会自动完成课程" in markdown
    assert "已有课程笔记应保留" not in markdown
    page.set_viewport_size({"width": 375, "height": 812})
    expect(card(page)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_evidence_backup_round_trip_and_legacy_v1_import(page):
    goto(page, "/lesson/fullstack-integration")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_label("课程笔记", exact=True).fill("备份中的已有笔记")
    fill_evidence(page, GO_EVIDENCE)
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    assert download.value.suggested_filename == "deep-ai-station-progress.json"
    backup = json.loads(Path(download.value.path()).read_text())
    assert backup["evidence"][0]["revision"] == GO_EVIDENCE["revision"]
    page.evaluate("key => localStorage.removeItem(key)", STORAGE_KEY)
    page.reload()
    page.get_by_role("button", name="Go", exact=True).click()
    expect_evidence(page, {})
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("")
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    upload_backup(page, backup)
    page.keyboard.press("Escape")
    expect_evidence(page, GO_EVIDENCE)
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("备份中的已有笔记")
    assert stored(page)["completed"] == backup["completed"]
    legacy = {
        "version": 1,
        "completed": ["fullstack-http"],
        "bookmarks": [],
        "notes": {"fullstack-integration": "旧版 v1 备份笔记"},
        "language": "go",
        "runs": [],
    }
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    upload_backup(page, legacy)
    page.keyboard.press("Escape")
    page.reload()
    expect_evidence(page, {})
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("旧版 v1 备份笔记")
    assert stored(page)["completed"] == ["fullstack-http"]
    assert not stored(page).get("evidence")


def test_evidence_card_is_limited_to_capstone_lessons_and_agent_python(page):
    goto(page, "/lesson/fullstack-http")
    expect(card(page)).to_have_count(0)
    page.get_by_role("button", name="Go", exact=True).click()
    goto(page, "/lesson/agent-agent-loop")
    expect(card(page)).to_have_count(0)
    goto(page, "/lesson/agent-research-agent")
    expect(card(page)).to_be_visible()
    expect(page.get_by_role("button", name="Go", exact=True)).to_have_count(0)
    fill_evidence(page, {"revision": "agent-python-evidence"})
    record = stored(page)["evidence"][0]
    assert record["lesson_id"] == "agent-research-agent"
    assert record["language"] == "python"
    assert stored(page)["language"] == "go"
    for lesson in ("agent-research-workflow", "agent-research-release", "fullstack-product"):
        goto(page, f"/lesson/{lesson}")
        expect(card(page)).to_be_visible()
        expect_evidence(page, {})
    goto(page, "/lesson/agent-research-agent")
    expect_evidence(page, {"revision": "agent-python-evidence"})
