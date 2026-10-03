"""Real lesson UI/localStorage/export flows for language-specific lab evidence."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e
KEY = "deep-ai-station:v1"
FIELDS = {
    "revision": "代码版本 / commit",
    "command": "验证命令",
    "success": "成功输入与实际结果",
    "failure": "失败输入与实际结果",
    "pending": "未验证事项",
}
LABS = [
    ("可运行 API 契约实验", ("fullstack-routing", "fullstack-validation")),
    ("可运行 SQLite 数据实验", ("fullstack-database", "fullstack-migrations")),
    ("可运行会话与授权实验", ("fullstack-auth", "fullstack-app-security")),
    ("可运行受限文本上传实验", ("fullstack-ai-rag",)),
    ("可运行 SSE 流式实验", ("fullstack-ai-stream", "fullstack-async")),
]
VALUES = {
    "revision": "fixture-python-evidence",
    "command": "uv run pytest -q\npython cli.py verify",
    "success": "保存进度后重启：记录仍存在。",
    "failure": "审计写入失败：进度与审计一起回滚。",
    "pending": "真实身份、生产数据库尚未验证。",
}


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        paid = []
        errors = []
        context.on(
            "request",
            lambda request: (
                paid.append(request.url)
                if any(
                    path in request.url
                    for path in ("/api/playground/run", "/api/playground/execute")
                )
                else None
            ),
        )
        context.on(
            "page", lambda opened: opened.on("pageerror", lambda error: errors.append(str(error)))
        )
        try:
            yield context.new_page()
            assert not paid, "Evidence must not request a model or sandbox"
            assert not errors
        finally:
            context.close()
            browser.close()


def goto(page, lesson, language="python"):
    page.goto(
        os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + f"/lesson/{lesson}?language={language}"
    )
    expect(page.locator(".lesson-article h1")).to_be_visible()


def card(page):
    return page.get_by_role("region", name="实验实践证据", exact=True)


def stored(page):
    return json.loads(page.evaluate("key => localStorage.getItem(key)", KEY))


def put(page, value, *, event=False):
    page.evaluate(
        """({key,value,event}) => {
      localStorage.setItem(key, JSON.stringify(value));
      if(event) window.dispatchEvent(new StorageEvent('storage', {key, storageArea:localStorage,newValue:JSON.stringify(value)}));
    }""",
        {"key": KEY, "value": value, "event": event},
    )


def record(index=0, **values):
    return {
        "lesson_id": f"fullstack-fixture-{index}",
        "language": "python",
        "revision": f"fixture-{index}",
        "command": "",
        "success": "",
        "failure": "",
        "pending": "",
        "updated_at": "2026-10-04T00:00:00.000Z",
        **values,
    }


def fill(page, values):
    for field, value in values.items():
        card(page).get_by_label(FIELDS[field], exact=True).fill(value)


def expect_fields(page, values):
    for field, label in FIELDS.items():
        expect(card(page).get_by_label(label, exact=True)).to_have_value(values.get(field, ""))


def open_settings(page):
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    return page.get_by_role("dialog")


def import_file(page, value, message="学习记录已导入"):
    page.locator('input[type="file"]').set_input_files(
        {
            "name": "lab-evidence.json",
            "mimeType": "application/json",
            "buffer": json.dumps(value, ensure_ascii=False).encode(),
        }
    )
    expect(page.get_by_role("dialog").get_by_role("status")).to_contain_text(message)


@pytest.mark.parametrize("title,lessons", LABS)
def test_all_nine_lab_lessons_offer_three_language_evidence_without_creating_records(
    page, title, lessons
):
    for lesson in lessons:
        for language, label in (("typescript", "TypeScript"), ("go", "Go"), ("python", "Python")):
            goto(page, lesson, language)
            expect(card(page)).to_be_visible()
            expect(card(page)).to_contain_text(title)
            expect(card(page)).to_contain_text(label)
            expect(page.get_by_role("region", name="毕业实践证据", exact=True)).to_have_count(0)
            expect_fields(page, {})
            expect(
                card(page).get_by_role("button", name="下载实践证据 Markdown", exact=True)
            ).to_be_disabled()
            assert not stored(page).get("evidence")
    goto(page, "fullstack-http")
    expect(card(page)).to_have_count(0)


def test_lab_evidence_preserves_other_progress_and_isolates_language_course_and_export(page):
    goto(page, "fullstack-database")
    page.get_by_label("课程笔记", exact=True).fill("已有学习笔记")
    initial = stored(page)
    initial.update(
        completed=["fullstack-http"],
        bookmarks=["kept-bookmark"],
        practice=[
            {
                "lesson_id": "fullstack-routing",
                "language": "go",
                "completed_at": "2026-10-04T00:00:00.000Z",
            }
        ],
    )
    put(page, initial, event=True)
    fill(page, VALUES)
    page.reload()
    expect_fields(page, VALUES)
    page.get_by_role("button", name="Go", exact=True).click()
    expect_fields(page, {})
    fill(page, {"command": "go test -race ./..."})
    goto(page, "fullstack-migrations")
    expect_fields(page, {})
    fill(page, {"failure": "迁移失败时保留旧版本"})
    goto(page, "fullstack-database")
    expect_fields(page, VALUES)
    after = stored(page)
    assert len(after["evidence"]) == 3
    for field in ("completed", "bookmarks", "notes", "practice", "runs"):
        assert after[field] == initial[field]
    with page.expect_download() as downloaded:
        card(page).get_by_role("button", name="下载实践证据 Markdown", exact=True).click()
    assert downloaded.value.suggested_filename == "fullstack-database-python-evidence.md"
    markdown = Path(downloaded.value.path()).read_text()
    assert markdown.startswith("# 实验实践证据")
    assert "可运行 SQLite 数据实验" in markdown and "未经平台核验" in markdown
    assert all(value in markdown for value in VALUES.values())
    assert "已有学习笔记" not in markdown
    page.set_viewport_size({"width": 375, "height": 812})
    expect(card(page)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_lab_backup_round_trip_and_legacy_records_are_kept(page):
    goto(page, "fullstack-routing", "go")
    legacy = stored(page)
    legacy["evidence"] = [record(index) for index in range(24)]
    put(page, legacy, event=True)
    fill(page, VALUES)
    open_settings(page)
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    backup = json.loads(Path(downloaded.value.path()).read_text())
    assert backup["version"] == 1 and len(backup["evidence"]) == 25
    assert backup["evidence"][:24] == legacy["evidence"]
    page.evaluate("key => localStorage.removeItem(key)", KEY)
    page.reload()
    expect_fields(page, {})
    open_settings(page)
    import_file(page, backup)
    page.keyboard.press("Escape")
    expect_fields(page, VALUES)
    assert stored(page)["evidence"] == backup["evidence"]


def test_full_capacity_still_allows_edit_and_clearing_reclaims_one_slot(page):
    goto(page, "fullstack-database")
    progress = stored(page)
    progress["evidence"] = [record(index) for index in range(47)] + [
        record(47, lesson_id="fullstack-database")
    ]
    put(page, progress, event=True)
    fill(page, {"command": "uv run pytest"})
    assert len(stored(page)["evidence"]) == 48
    goto(page, "fullstack-routing")
    expect(card(page).get_by_label(FIELDS["revision"], exact=True)).to_be_disabled()
    expect(card(page).get_by_role("status")).to_contain_text("48 条容量")
    goto(page, "fullstack-database")
    fill(page, {field: "" for field in FIELDS})
    assert len(stored(page)["evidence"]) == 47
    page.reload()
    expect_fields(page, {})
    goto(page, "fullstack-routing")
    fill(page, {"revision": "new-slot"})
    assert len(stored(page)["evidence"]) == 48
    saved = stored(page)
    open_settings(page)
    import_file(page, {**saved, "evidence": saved["evidence"] + [record(99)]}, "学习记录格式不正确")
    assert stored(page) == saved


def test_field_edit_reads_pending_external_storage_and_late_event_cannot_roll_back(page):
    goto(page, "fullstack-database")
    fill(page, {"revision": "initial"})
    old = stored(page)
    other = {
        **old,
        "notes": {"fullstack-http": "another tab note"},
        "evidence": [*old["evidence"], record(99, revision="another tab evidence")],
    }
    other["evidence"][0] = {**other["evidence"][0], "pending": "another tab field"}
    put(page, other)  # No storage event delivered yet.
    fill(page, {"command": "new current field"})
    merged = stored(page)
    assert merged["notes"] == other["notes"]
    assert merged["evidence"][1] == other["evidence"][1]
    expect(card(page).get_by_label(FIELDS["pending"], exact=True)).to_have_value(
        "another tab field"
    )
    page.evaluate(
        """({key,old}) => window.dispatchEvent(new StorageEvent('storage',
        {key,storageArea:localStorage,newValue:JSON.stringify(old)}))""",
        {"key": KEY, "old": old},
    )
    expect(card(page).get_by_label(FIELDS["command"], exact=True)).to_have_value(
        "new current field"
    )
    assert stored(page) == merged
    # Export also sees a persisted update whose event has not yet arrived.
    newer = {**merged, "notes": {**merged["notes"], "fullstack-routing": "export latest"}}
    put(page, newer)
    open_settings(page)
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    assert json.loads(Path(downloaded.value.path()).read_text()) == newer


def test_failed_persistence_keeps_memory_through_late_event_and_export(page):
    goto(page, "fullstack-database")
    fill(page, {"revision": "persisted"})
    before = stored(page)
    page.evaluate("""() => {
        const original=Storage.prototype.setItem;
        window.restoreEvidenceTestStorage=()=>{Storage.prototype.setItem=original;delete window.restoreEvidenceTestStorage;};
        Storage.prototype.setItem=function(key,value) {
          if(key==='deep-ai-station:v1') throw new DOMException('quota','QuotaExceededError');
          return original.call(this,key,value);
        };
    }""")
    fill(page, {"command": "unsaved command", "pending": "unsaved second field"})
    page.evaluate(
        """({key,old}) => window.dispatchEvent(new StorageEvent('storage',
      {key,storageArea:localStorage,newValue:JSON.stringify(old)}))""",
        {"key": KEY, "old": before},
    )
    expect(card(page).get_by_label(FIELDS["command"], exact=True)).to_have_value("unsaved command")
    expect(page.get_by_role("alert")).to_contain_text("浏览器存储不可用")
    assert stored(page) == before
    open_settings(page)
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    data = json.loads(Path(downloaded.value.path()).read_text())
    assert data["evidence"][0]["command"] == "unsaved command"
    assert data["evidence"][0]["pending"] == "unsaved second field"
    page.keyboard.press("Escape")
    page.evaluate("window.restoreEvidenceTestStorage()")
    fill(page, {"success": "persisted after recovery"})
    recovered = stored(page)["evidence"][0]
    assert recovered["command"] == "unsaved command"
    assert recovered["pending"] == "unsaved second field"
    assert recovered["success"] == "persisted after recovery"
    expect(page.get_by_role("alert")).to_have_count(0)


def test_oversized_valid_backup_is_complete_with_warning_and_import_does_not_overwrite(page):
    goto(page, "fullstack-database")
    progress = stored(page)
    progress["evidence"] = [
        record(
            index,
            revision="\0" * 200,
            command="\0" * 2000,
            success="\0" * 2000,
            failure="\0" * 2000,
            pending="\0" * 2000,
        )
        for index in range(48)
    ]
    put(page, progress, event=True)
    open_settings(page)
    with page.expect_download() as downloaded:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    content = Path(downloaded.value.path()).read_bytes()
    assert len(content) > 2_000_000 and json.loads(content) == progress
    expect(page.get_by_role("dialog").get_by_role("status")).to_contain_text("无法直接导回当前版本")
    import_file(page, progress, "文件过大")
    assert stored(page) == progress


def test_two_real_tabs_keep_independent_evidence_and_current_fields(page):
    goto(page, "fullstack-database")
    fill(page, {"revision": "python tab"})
    second = page.context.new_page()
    try:
        goto(second, "fullstack-migrations", "go")
        fill(second, {"command": "go tab command"})
        fill(page, {"failure": "python tab failure"})
        second.reload()
        expect_fields(second, {"command": "go tab command"})
        page.reload()
        expect_fields(page, {"revision": "python tab", "failure": "python tab failure"})
        records = stored(page)["evidence"]
        assert len(records) == 2
        assert {(item["lesson_id"], item["language"]) for item in records} == {
            ("fullstack-database", "python"),
            ("fullstack-migrations", "go"),
        }
    finally:
        second.close()


def test_same_page_navigation_never_moves_old_fields_to_new_lesson_or_language(page):
    goto(page, "fullstack-database")
    fill(page, {"revision": "first python record"})
    page.evaluate("""() => {history.pushState({},'', '/lesson/fullstack-migrations?language=go');
        window.dispatchEvent(new PopStateEvent('popstate'));}""")
    expect(card(page).get_by_label(FIELDS["revision"], exact=True)).to_have_attribute(
        "id", "evidence-fullstack-migrations-go-revision"
    )
    expect_fields(page, {})
    fill(page, {"revision": "second go record"})
    page.go_back()
    expect(card(page).get_by_label(FIELDS["revision"], exact=True)).to_have_attribute(
        "id", "evidence-fullstack-database-python-revision"
    )
    expect_fields(page, {"revision": "first python record"})
    assert len(stored(page)["evidence"]) == 2
