"""Curated reading becomes personal course notes without granting learning credit.

Services belong to the shared E2E runner. Fixed feed responses avoid external
reading/model requests; real React, storage, backup and course navigation run.
"""

import copy
import json
import re

import pytest
from playwright.sync_api import expect
from test_feed_course_browser import CURATED, ITEMS, card, feed_payload
from test_progress_write_browser import (
    EPOCH_A,
    EPOCH_B,
    external,
    goto,
    release_storage_event,
)
from test_progress_write_browser import (
    page as page,
)
from test_run_history_browser import (
    export_record,
    import_record,
    progress,
    put_progress,
    record,
    stored,
)

pytestmark = pytest.mark.e2e
KEY = "deep-ai-station:v1"
OPEN = "写阅读摘记"
PREVIEW = "预览将追加的内容"
SAVE = "追加到本课笔记"
SAVED = "已追加到本课笔记"
UNDERSTANDING = (
    "  我的理解：资料需要结合本课实践核对。\n````\n<script>window.readingSentinel=1</script> 😀  "
)
QUESTION = "  准备验证：中文、e\u0301 与原文引用如何保留？\n<img src=x onerror=alert(1)>  "
MAPPINGS = (
    ("agents-sdk", "agent-agent-loop", "python"),
    ("mcp-guide", "agent-mcp", "python"),
    ("langgraph", "agent-state-machine", "python"),
    ("evals", "agent-datasets", "python"),
    ("fastapi-guide", "fullstack-routing", "python"),
    ("hono-guide", "fullstack-routing", "typescript"),
    ("go-guide", "fullstack-async", "go"),
    ("jotai-guide", "fullstack-jotai", "go"),
)


def baseline(**values):
    return progress(
        **{
            "history_reset_id": EPOCH_A,
            "language": "go",
            "completed": ["agent-agent-loop"],
            "notes": {"agent-mcp": "原有 MCP 笔记", "fullstack-routing": "原有路由笔记"},
            "runs": [record(1)],
            "practice": [
                {
                    "lesson_id": "fullstack-routing",
                    "language": "go",
                    "completed_at": "2026-10-04T00:00:00.000Z",
                }
            ],
            "evidence": [
                {
                    "lesson_id": "agent-mcp",
                    "language": "python",
                    "revision": "固定旧版本",
                    "command": "",
                    "success": "",
                    "failure": "",
                    "pending": "仍需独立验证",
                    "updated_at": "2026-10-04T00:00:00.000Z",
                }
            ],
            **values,
        }
    )


def seed(page, value, path="/feed", items=None):
    page.route("**/api/feed*", lambda route: route.fulfill(json=feed_payload(items)))
    goto(page, "/")
    put_progress(page, value)
    goto(page, path)


def note_region(page, item_id="mcp-guide"):
    return card(page, item_id).get_by_role("region", name="阅读摘记", exact=True)


def open_note(page, item_id="mcp-guide", *, keyboard=False):
    button = card(page, item_id).get_by_role("button", name=OPEN, exact=True)
    if keyboard:
        button.focus()
        page.keyboard.press("Enter")
    else:
        button.click()
    region = note_region(page, item_id)
    expect(region).to_be_visible()
    return region


def write_and_preview(region, understanding=UNDERSTANDING, question=QUESTION):
    region.get_by_label("我的理解", exact=True).fill(understanding)
    region.get_by_label("准备验证的问题", exact=True).fill(question)
    region.get_by_role("button", name=PREVIEW, exact=True).click()
    preview = region.get_by_label("阅读摘记预览", exact=True)
    expect(preview).to_be_visible()
    return preview.text_content()


def marker(entry):
    matches = re.findall(r"^### 阅读摘记 ([0-9a-f-]{36})$", entry, re.MULTILINE)
    assert len(matches) == 1
    return matches[0]


def only_target_note_changed(before, after, lesson_id):
    assert {key: value for key, value in before.items() if key != "notes"} == {
        key: value for key, value in after.items() if key != "notes"
    }
    assert {key: value for key, value in before["notes"].items() if key != lesson_id} == {
        key: value for key, value in after["notes"].items() if key != lesson_id
    }


def test_all_eight_mappings_expose_both_entries_without_writing_progress(page):
    value = baseline(bookmarks=[item["id"] for item in CURATED], savedItems=copy.deepcopy(CURATED))
    seed(page, value)
    for path in ("/feed", "/library?tab=bookmarks"):
        if path != "/feed":
            goto(page, path)
        expect(page.get_by_role("button", name=OPEN, exact=True)).to_have_count(8)
        for item_id, lesson_id, language in MAPPINGS:
            source = card(page, item_id)
            expect(source.get_by_role("button", name=OPEN, exact=True)).to_be_enabled()
            href = "/lesson/" + lesson_id
            if lesson_id.startswith("fullstack") and item_id != "jotai-guide":
                href += "?language=" + language
            expect(source.get_by_role("link", name="配套课程：", exact=False)).to_have_attribute(
                "href", href
            )
        assert page.locator("a a").count() == 0
        assert stored(page) == value


@pytest.mark.parametrize(
    ("item_id", "lesson_id", "path"),
    [
        ("mcp-guide", "agent-mcp", "/feed"),
        ("fastapi-guide", "fullstack-routing", "/library?tab=bookmarks"),
    ],
)
def test_reading_preview_save_course_and_v1_backup_are_one_learning_loop(
    page, item_id, lesson_id, path
):
    source = ITEMS[item_id]
    value = baseline(bookmarks=[item_id], savedItems=[copy.deepcopy(source)])
    seed(page, value, path)
    if item_id == "mcp-guide":
        page.set_viewport_size({"width": 375, "height": 812})
    source_card = card(page, item_id)
    original = source_card.get_by_role("link", name=source["title"], exact=False)
    expect(original).to_have_attribute("href", source["url"])
    expect(original).to_have_attribute("target", "_blank")
    expect(original).to_have_attribute("rel", "noreferrer")
    region = open_note(page, item_id, keyboard=True)
    expect(region.get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
    understanding = region.get_by_label("我的理解", exact=True)
    question = region.get_by_label("准备验证的问题", exact=True)
    expect(understanding).to_have_attribute("maxlength", "1500")
    expect(question).to_have_attribute("maxlength", "500")
    understanding.fill(UNDERSTANDING)
    expect(region.get_by_role("button", name=PREVIEW, exact=True)).to_be_disabled()
    question.fill(QUESTION)
    preview_button = region.get_by_role("button", name=PREVIEW, exact=True)
    preview_button.focus()
    page.keyboard.press("Enter")
    preview = region.get_by_label("阅读摘记预览", exact=True)
    expect(preview).to_be_visible()
    first_entry = preview.text_content()
    assert UNDERSTANDING in first_entry and QUESTION in first_entry
    assert preview.locator("script,img,a").count() == 0
    assert page.evaluate("typeof window.readingSentinel") == "undefined"
    assert stored(page) == value
    # Editing after preview requires a new explicit preview, preserving this draft ID.
    question.fill(QUESTION + "\n下一步在本课运行固定案例。")
    expect(preview).to_have_count(0)
    expect(region.get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
    preview_button.click()
    expect(preview).to_be_visible()
    entry = preview.text_content()
    assert marker(entry) == marker(first_entry)
    for text in (source["title"], source["source"], source["url"], lesson_id, "参考语言：python"):
        assert text in entry
    assert "个人阅读摘记；不代表平台核验或实践完成。" in entry
    # Keep the unsaved preview mounted: the long MCP URL exposed a grid-track
    # overflow that disappears once saving removes the preview. Cover both entry containers.
    page.set_viewport_size({"width": 375, "height": 812})
    expect(region).to_be_visible()
    expect(understanding).to_be_visible()
    expect(question).to_be_visible()
    expect(preview).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.set_viewport_size({"width": 1280, "height": 1000})
    save = region.get_by_role("button", name=SAVE, exact=True)
    save.focus()
    page.keyboard.press("Enter")
    expect(region.get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    only_target_note_changed(value, after, lesson_id)
    note = after["notes"][lesson_id]
    assert note == value["notes"][lesson_id] + "\n\n" + entry
    assert note.count("### 阅读摘记 " + marker(entry)) == 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    link = region.get_by_role("link", name="查看本课笔记", exact=True)
    href = "/lesson/" + lesson_id
    if item_id == "fastapi-guide":
        href += "?language=python"
    expect(link).to_have_attribute("href", href)
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**" + href)
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    expect(page.get_by_role("link", name="在实验空间编辑", exact=True)).to_be_visible()
    assert stored(page)["language"] == ("go" if item_id == "mcp-guide" else "python")
    page.reload()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    page.set_viewport_size({"width": 1280, "height": 1000})
    page.get_by_role("link", name="我的学习库", exact=True).click()
    page.get_by_role("button", name="我的笔记", exact=True).click()
    expect(page.locator(".notes-grid article").filter(has_text=lesson_id)).to_contain_text(
        UNDERSTANDING
    )
    backup = export_record(page)
    assert backup["version"] == 1 and backup["notes"][lesson_id] == note
    page.evaluate("key => localStorage.removeItem(key)", KEY)
    goto(page, "/lesson/" + lesson_id)
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("")
    dialog = import_record(page, backup)
    expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    final = stored(page)
    for key in ("completed", "practice", "runs", "evidence", "bookmarks", "savedItems"):
        assert final[key] == value[key]


def test_rss_and_forged_url_keep_original_reading_but_never_gain_note_entry(page):
    forged = {**ITEMS["agents-sdk"], "url": "https://example.invalid/impostor"}
    rss = {**ITEMS["go-guide"], "id": "reading-rss", "kind": "news"}
    unknown = {**ITEMS["hono-guide"], "id": "reading-unknown"}
    items = [ITEMS["mcp-guide"], forged, rss, unknown]
    value = baseline(bookmarks=[item["id"] for item in items], savedItems=copy.deepcopy(items))
    seed(page, value, items=items)
    for item in (forged, rss, unknown):
        source = page.get_by_role("article").filter(
            has=page.get_by_role("heading", name=item["title"], exact=True)
        )
        expect(source.get_by_role("link", name=item["title"], exact=False)).to_have_attribute(
            "href", item["url"]
        )
        expect(source.get_by_role("button", name=OPEN, exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name=OPEN, exact=True)).to_have_count(1)
    # Saved snapshots still work if the current feed API cannot return anything.
    page.route("**/api/feed*", lambda route: route.fulfill(status=503, json={"detail": "固定失败"}))
    goto(page, "/library?tab=bookmarks")
    expect(page.get_by_role("article")).to_have_count(4)
    expect(page.get_by_role("button", name=OPEN, exact=True)).to_have_count(1)
    expect(card(page, "mcp-guide").get_by_role("button", name=OPEN, exact=True)).to_be_enabled()
    assert stored(page) == value


@pytest.mark.parametrize("stage", ["preview", "save"])
def test_silent_reset_rejects_old_draft_before_preview_or_save_then_explicit_open_is_fresh(
    page, stage
):
    seed(page, baseline())
    region = open_note(page)
    region.get_by_label("我的理解", exact=True).fill("old-reading-draft")
    region.get_by_label("准备验证的问题", exact=True).fill("old-reading-question")
    if stage == "save":
        region.get_by_role("button", name=PREVIEW, exact=True).click()
    old = stored(page)
    reset = progress(
        history_reset_id=EPOCH_B, language="typescript", notes={"fullstack-http": "新记录"}
    )
    external(page, reset)
    region.get_by_role("button", name=PREVIEW if stage == "preview" else SAVE, exact=True).click()
    expect(card(page, "mcp-guide")).to_contain_text("未保存")
    expect(card(page, "mcp-guide").get_by_label("我的理解", exact=True)).to_have_count(0)
    expect(card(page, "mcp-guide").get_by_label("阅读摘记预览", exact=True)).to_have_count(0)
    assert stored(page) == reset
    # A late event carrying the old payload may not revive old content.
    release_storage_event(page, old)
    assert stored(page) == reset
    region = open_note(page)
    expect(region.get_by_label("我的理解", exact=True)).to_have_value("")
    entry = write_and_preview(region, "新上下文明确输入", "验证当前课程，不恢复旧摘记")
    region.get_by_role("button", name=SAVE, exact=True).click()
    expect(region.get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    only_target_note_changed(reset, after, "agent-mcp")
    assert after["notes"]["agent-mcp"] == entry
    assert "old-reading" not in json.dumps(after)


def test_latest_capacity_rejections_keep_original_draft_then_append_without_losing_other_notes(
    page,
):
    seed(page, baseline())
    region = open_note(page)
    entry = write_and_preview(region)
    original = stored(page)
    full_note = copy.deepcopy(original)
    full_note["notes"]["agent-mcp"] = "满" * 10000
    external(page, full_note)
    region.get_by_role("button", name=SAVE, exact=True).click()
    expect(region).to_contain_text("空间不足")
    expect(region).to_contain_text("保留草稿")
    expect(region.get_by_label("我的理解", exact=True)).to_have_value(UNDERSTANDING)
    expect(region.get_by_label("阅读摘记预览", exact=True)).to_have_text(entry)
    assert stored(page) == full_note
    room = copy.deepcopy(original)
    room["notes"]["fullstack-launch"] = "另一标签页刚写的笔记"
    put_progress(page, room)
    expect(region.get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
    full_keys = {**room, "notes": {f"agent-legacy-{index}": "旧课笔记" for index in range(1000)}}
    external(page, full_keys)
    region.get_by_role("button", name=SAVE, exact=True).click()
    expect(region).to_contain_text("空间不足")
    expect(region.get_by_label("阅读摘记预览", exact=True)).to_have_text(entry)
    assert stored(page) == full_keys
    newest = copy.deepcopy(full_keys)
    del newest["notes"]["agent-legacy-0"]
    put_progress(page, newest)
    expect(region.get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
    region.get_by_role("button", name=SAVE, exact=True).click()
    expect(region.get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    only_target_note_changed(newest, after, "agent-mcp")
    assert len(after["notes"]) == 1000 and after["notes"]["agent-mcp"] == entry


def test_storage_failure_consumes_memory_draft_and_export_contains_the_new_note(page):
    seed(page, baseline())
    region = open_note(page)
    entry = write_and_preview(region)
    disk = stored(page)
    page.evaluate(
        """key => {
          const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(name, value) {
            if (name === key) throw new DOMException('fixed quota failure', 'QuotaExceededError');
            return original.call(this, name, value);
          };
        }""",
        KEY,
    )
    region.get_by_role("button", name=SAVE, exact=True).click()
    expect(region.get_by_role("button", name="已追加到本页内存", exact=True)).to_be_disabled()
    expect(region).to_contain_text("尚未保存到浏览器")
    expect(region).to_contain_text("请导出学习记录备份")
    expect(page.locator("body")).to_contain_text("浏览器存储不可用")
    backup = export_record(page)
    only_target_note_changed(disk, backup, "agent-mcp")
    assert backup["notes"]["agent-mcp"] == disk["notes"]["agent-mcp"] + "\n\n" + entry
    assert stored(page) == disk
    assert backup["notes"]["agent-mcp"].count("### 阅读摘记 " + marker(entry)) == 1


def test_consumed_draft_stays_consumed_after_manual_removal_and_again_creates_new_record(page):
    seed(page, baseline())
    region = open_note(page)
    before = stored(page)
    discarded = write_and_preview(region)
    region.get_by_role("button", name="放弃草稿", exact=True).click()
    expect(card(page, "mcp-guide").get_by_role("button", name=OPEN, exact=True)).to_be_focused()
    assert stored(page) == before
    region = open_note(page)
    expect(region.get_by_label("我的理解", exact=True)).to_have_value("")
    first = write_and_preview(region)
    assert marker(first) != marker(discarded)
    # Two synchronous DOM clicks are a deterministic duplicate user-action seam.
    region.get_by_role("button", name=SAVE, exact=True).evaluate(
        "button => { button.click(); button.click(); }"
    )
    expect(region.get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    saved = stored(page)
    assert saved["notes"]["agent-mcp"].count("### 阅读摘记 " + marker(first)) == 1
    removed = copy.deepcopy(saved)
    removed["notes"]["agent-mcp"] = "用户明确删除了先前摘要，保留这段新笔记"
    put_progress(page, removed)
    expect(region.get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    expect(region.get_by_label("我的理解", exact=True)).to_have_count(0)
    assert stored(page) == removed
    region.get_by_role("button", name="再写一条", exact=True).click()
    expect(region.get_by_label("我的理解", exact=True)).to_have_value("")
    expect(region.get_by_label("准备验证的问题", exact=True)).to_have_value("")
    second = write_and_preview(region, "再次阅读后的新观察", "下一步做新的固定验证")
    assert marker(second) != marker(first)
    region.get_by_role("button", name=SAVE, exact=True).click()
    expect(region.get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    final = stored(page)
    only_target_note_changed(removed, final, "agent-mcp")
    assert final["notes"]["agent-mcp"] == removed["notes"]["agent-mcp"] + "\n\n" + second
    assert marker(first) not in final["notes"]["agent-mcp"]
    region.get_by_role("button", name="收起摘记", exact=True).click()
    expect(card(page, "mcp-guide").get_by_role("button", name=OPEN, exact=True)).to_be_focused()
    assert stored(page) == final
