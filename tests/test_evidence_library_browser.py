"""Evidence index navigation, legacy data, current export identity, and plain text.

The E2E runner owns services; this suite only uses its existing headless fixtures.
"""

import copy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect
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
    stored,
)
from test_run_history_browser import (
    record as run_record,
)

pytestmark = pytest.mark.e2e
KEY = "deep-ai-station:v1"
FIELDS = ("revision", "command", "success", "failure", "pending")
PENDING = "只看填写了未验证说明的记录"
DOWNLOAD = "导出此条记录 JSON"
LANGUAGES = {"typescript": "TypeScript", "go": "Go", "python": "Python"}


def evidence(lesson="fullstack-routing", language="python", **fields):
    return {
        "lesson_id": lesson,
        "language": language,
        "revision": "fixture-revision",
        "command": "公开验证命令",
        "success": "固定成功观察",
        "failure": "固定失败观察",
        "pending": "",
        "updated_at": "2026-10-04T12:00:00.000Z",
        **fields,
    }


def baseline(records, **values):
    return progress(
        history_reset_id=EPOCH_A,
        language="typescript",
        completed=["fullstack-http"],
        notes={"fullstack-http": "现有课程笔记"},
        runs=[run_record(1)],
        practice=[
            {
                "lesson_id": "fullstack-http",
                "language": "go",
                "completed_at": "2026-10-04T00:00:00.000Z",
            }
        ],
        evidence=records,
        **values,
    )


def index(page):
    return page.get_by_role("region", name="实践证据索引", exact=True)


def row(page, lesson="fullstack-routing", language="python"):
    return index(page).get_by_role("article", name=f"{lesson} · {language}", exact=True)


def row_keys(page):
    return (
        index(page)
        .get_by_role("article")
        .evaluate_all("items => items.map(item => [item.dataset.lessonId, item.dataset.language])")
    )


def open_index(page, value):
    goto(page, "/")
    put_progress(page, value)
    goto(page, "/library?tab=evidence")
    expect(page.get_by_role("button", name="实践证据", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(index(page)).to_be_visible()


def show_details(card):
    summary = card.locator("summary").filter(has_text="查看填写内容")
    summary.click()
    expect(card.locator("details")).to_have_attribute("open", "")


def download_record(page, card, expected):
    with page.expect_download() as event:
        card.get_by_role("button", name=DOWNLOAD, exact=True).click()
    assert event.value.suggested_filename == f"practice-evidence-{expected['language']}.json"
    result = json.loads(Path(event.value.path()).read_bytes())
    assert result == expected
    assert set(result) == {"lesson_id", "language", "updated_at", *FIELDS}
    return result


def select_filter(page, label, choice):
    index(page).get_by_label(label, exact=True).select_option(label=choice)


def assert_learning_facts_unchanged(before, after):
    for key in ("completed", "practice", "notes", "runs", "evidence", "history_reset_id"):
        assert after.get(key) == before.get(key), key


def test_index_filters_details_links_and_other_learning_facts(page):
    records = [
        evidence(language="typescript", updated_at="2026-10-01T00:00:00.000Z"),
        evidence(language="python", pending="   ", updated_at="2026-10-03T00:00:00.000Z"),
        evidence("agent-research-agent", pending="无", updated_at="2026-10-04T00:00:00.000Z"),
        evidence(language="go", pending="待复查", updated_at="2026-10-03T00:00:00.000Z"),
        evidence("fullstack-database", "go", updated_at="2026-10-03T00:00:00.000Z"),
    ]
    original = baseline(records)
    open_index(page, original)
    expect(index(page).get_by_role("article")).to_have_count(5)
    assert row_keys(page) == [
        ["agent-research-agent", "python"],
        ["fullstack-database", "go"],
        ["fullstack-routing", "go"],
        ["fullstack-routing", "python"],
        ["fullstack-routing", "typescript"],
    ]
    select_filter(page, "证据路线", "AI 全栈")
    expect(index(page).get_by_role("article")).to_have_count(4)
    select_filter(page, "证据语言", "Go")
    expect(index(page).get_by_role("article")).to_have_count(2)
    index(page).get_by_label(PENDING, exact=True).check()
    assert row_keys(page) == [["fullstack-routing", "go"]]
    select_filter(page, "证据路线", "AI Agent")
    expect(index(page).get_by_role("article")).to_have_count(0)
    index(page).get_by_role("button", name="清除筛选", exact=True).click()
    expect(index(page).get_by_role("article")).to_have_count(5)
    agent = row(page, "agent-research-agent")
    show_details(agent)
    for field in FIELDS:
        assert records[2][field] in agent.text_content()
    download_record(page, agent, records[2])
    assert stored(page) == original, "Browsing, filtering, and export must not write progress"
    go = row(page, language="go").get_by_role("link", name="打开课程：", exact=False)
    href = go.get_attribute("href")
    assert urlparse(href).path == "/lesson/fullstack-routing"
    assert parse_qs(urlparse(href).query) == {"language": ["go"]}
    go.click()
    expect(page.get_by_role("region", name="实验实践证据", exact=True)).to_contain_text(
        "当前记录：Go"
    )
    expect(page.get_by_label("验证命令", exact=True)).to_have_value(records[3]["command"])
    assert stored(page)["language"] == "go"
    assert_learning_facts_unchanged(original, stored(page))
    page.get_by_role("link", name="我的学习库", exact=True).click()
    page.get_by_role("button", name="实践证据", exact=True).click()
    link = row(page, "agent-research-agent").get_by_role("link", name="打开课程：", exact=False)
    expect(link).to_have_attribute("href", "/lesson/agent-research-agent")
    link.click()
    expect(page.get_by_role("region", name="毕业实践证据", exact=True)).to_be_visible()
    assert stored(page)["language"] == "go", (
        "Agent navigation must preserve the fullstack preference"
    )
    assert_learning_facts_unchanged(original, stored(page))


@pytest.mark.parametrize(
    "catalog_case", ["normal", "duplicate-id", "wrong-parent", "unsupported-language"]
)
def test_legacy_records_roundtrip_and_ambiguous_catalog_never_creates_bad_links(page, catalog_case):
    if catalog_case != "normal":

        def changed_curriculum(route):
            response = route.fetch()
            assert response.status == 200
            payload = response.json()
            fullstack = next(track for track in payload["tracks"] if track["id"] == "fullstack")
            lesson = next(
                item for item in fullstack["lessons"] if item["id"] == "fullstack-routing"
            )
            if catalog_case == "duplicate-id":
                fullstack["lessons"].append(copy.deepcopy(lesson))
            elif catalog_case == "wrong-parent":
                fullstack["lessons"].remove(lesson)
                agent = next(track for track in payload["tracks"] if track["id"] == "agent")
                agent["lessons"].append(lesson)
            else:
                fullstack["languages"] = [
                    language for language in fullstack["languages"] if language != "go"
                ]
                lesson["snippets"]["go"] = ""
            route.fulfill(response=response, json=payload)

        page.route("**/api/curriculum", changed_curriculum)
    empty = evidence("fullstack-empty-fixture", **dict.fromkeys(FIELDS, ""))
    records = [
        evidence("fullstack-unknown-fixture", pending="旧未知课程的原文"),
        evidence("fullstack-legacy--0-", updated_at="Oct 03 2026 12:34:56 GMT+0000"),
        empty,
        evidence(language="go"),
        evidence("fullstack-http"),
        evidence("agent-research-agent"),
    ]
    legacy = baseline(records)
    del legacy["history_reset_id"]
    goto(page, "/library?tab=evidence")
    dialog = import_record(page, legacy)
    expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    expect(index(page).get_by_role("article")).to_have_count(6)
    imported = stored(page)
    assert imported["evidence"] == records
    for item in records[:3]:
        card = row(page, item["lesson_id"], item["language"])
        expect(card.get_by_role("link")).to_have_count(0)
        expect(card).to_contain_text("当前课程目录未匹配")
    expect(row(page, empty["lesson_id"])).to_contain_text("旧记录：尚未填写内容")
    expect(row(page, "fullstack-legacy--0-")).to_contain_text(records[1]["updated_at"])
    expect(row(page, "agent-research-agent").get_by_role("link")).to_have_count(1)
    http = row(page, "fullstack-http")
    expect(http).to_contain_text("本课当前没有此记录的编辑入口")
    expect(http.get_by_role("link")).to_have_attribute(
        "href", "/lesson/fullstack-http?language=python"
    )
    target = row(page, language="go")
    expect(target.get_by_role("link")).to_have_count(1 if catalog_case == "normal" else 0)
    if catalog_case == "normal":
        for item in records:
            download_record(page, row(page, item["lesson_id"], item["language"]), item)
        backup = export_record(page)
        assert backup["evidence"] == records
        dialog = import_record(page, backup)
        expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
        dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
        assert stored(page)["evidence"] == records
        for key in ("completed", "practice", "notes", "runs", "language"):
            assert stored(page)[key] == backup[key]
    else:
        assert stored(page) == imported


@pytest.mark.parametrize("change", ["same-epoch", "reset-same-key", "deleted", "memory-rescue"])
def test_record_export_reads_latest_identity_or_rejects_deleted_and_preserves_memory(page, change):
    original_record = evidence(command="旧卡片命令")
    original = baseline([original_record])
    open_index(page, original)
    card = row(page)
    expect(card).to_be_visible()
    latest_record = {**original_record, "command": "最新导出命令", "pending": "最新待验证说明"}
    if change == "memory-rescue":
        card.get_by_role("link", name="打开课程：", exact=False).click()
        expect(page.get_by_label("验证命令", exact=True)).to_have_value("旧卡片命令")
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
        page.get_by_label("验证命令", exact=True).fill("仅在内存的导出命令")
        expect(page.locator("body")).to_contain_text("浏览器存储不可用")
        page.get_by_role("link", name="我的学习库", exact=True).click()
        page.get_by_role("button", name="实践证据", exact=True).click()
        rescue = export_record(page)
        latest_record = rescue["evidence"][0]
        assert latest_record["command"] == "仅在内存的导出命令"
        download_record(page, row(page), latest_record)
        assert stored(page) == disk
        return
    latest = {**original, "evidence": [latest_record]}
    if change != "same-epoch":
        latest["history_reset_id"] = EPOCH_B
    if change == "deleted":
        latest["evidence"] = []
    external(page, latest)
    if change == "deleted":
        downloads = []
        page.on("download", lambda event: downloads.append(event))
        card.get_by_role("button", name=DOWNLOAD, exact=True).click()
        expect(index(page)).to_contain_text("记录已删除，未下载")
        expect(index(page).get_by_role("article")).to_have_count(0)
        page.evaluate(
            """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"""
        )
        assert downloads == []
        expect(index(page).get_by_role("button", name="清除筛选", exact=True)).to_have_count(0)
    else:
        download_record(page, card, latest_record)
        show_details(row(page))
        expect(row(page)).to_contain_text("最新导出命令")
        expect(row(page)).not_to_contain_text("旧卡片命令")
    assert stored(page) == latest
    release_storage_event(page, original)
    assert stored(page) == latest
    expect(index(page).get_by_role("article")).to_have_count(0 if change == "deleted" else 1)


def test_mobile_content_is_plain_text_and_pending_is_only_a_literal_filter(page):
    payloads = {
        "revision": "revision-```-😀",
        "command": '<img src="x" onerror="window.evidenceExecuted=true">\n<script>window.evidenceExecuted=true</script>',
        "success": "长中文😀`" * 130,
        "failure": "https://example.invalid/" + "x" * 1000,
        "pending": "无",
    }
    records = [
        evidence(**payloads),
        evidence(
            "agent-research-agent", **{**dict.fromkeys(FIELDS, ""), "pending": "只记录尚未验证"}
        ),
        evidence("fullstack-empty-fixture", **dict.fromkeys(FIELDS, "")),
    ]
    original = baseline(records)
    page.set_viewport_size({"width": 375, "height": 812})
    open_index(page, original)
    expect(index(page)).to_contain_text("未经平台核验")
    card = row(page)
    show_details(card)
    content = card.text_content()
    assert all(value in content for value in payloads.values())
    expect(index(page).locator("script, img, iframe")).to_have_count(0)
    expect(index(page).locator('a[href^="https://example.invalid"]')).to_have_count(0)
    assert not page.evaluate("window.evidenceExecuted === true")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    index(page).get_by_label(PENDING, exact=True).check()
    expect(index(page).get_by_role("article")).to_have_count(2)
    select_filter(page, "证据路线", "AI Agent")
    expect(index(page).get_by_role("article")).to_have_count(1)
    assert row_keys(page) == [["agent-research-agent", "python"]]
    assert stored(page) == original
    page.get_by_role("button", name="我的笔记", exact=True).click()
    expect(page.get_by_text("现有课程笔记", exact=True)).to_be_visible()
    page.get_by_role("button", name="完成记录", exact=True).click()
    expect(page.get_by_role("heading", name="HTTP 与 API 契约", exact=True)).to_be_visible()
    page.get_by_role("button", name="实践证据", exact=True).click()
    expect(index(page)).to_be_visible()
    assert stored(page) == original
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
