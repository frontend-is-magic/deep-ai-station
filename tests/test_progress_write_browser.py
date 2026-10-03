"""Latest learning writes preserve unrelated data and reject stale content after resets.

Fixed reports exercise storage and request ownership without model or sandbox calls.
Server lifecycle and port ownership belong to the E2E runner.
"""

import copy
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright
from test_run_history_browser import (
    export_record,
    progress,
    put_progress,
    record,
    stored,
)

pytestmark = pytest.mark.e2e
KEY = "deep-ai-station:v1"
EPOCH_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EPOCH_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EPOCH_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TIME = "2026-10-04T12:00:00.000Z"
OLD = "old-context-private-fixture"
ARTICLE = {
    "id": "progress-write-fixture",
    "title": "学习记录写入的公开资料",
    "summary": "用于收藏明确意图的固定资料。",
    "source": "固定公开课程",
    "url": "https://www.python.org/doc/",
    "track": "agent",
    "tags": ["Python"],
    "kind": "guide",
    "published": None,
}
MODES = {
    "tool": {
        "lesson": "agent-tool-contract",
        "mode": "tool-contract",
        "region": "工具契约实验",
        "run": "运行工具校验",
        "running": "正在校验…",
        "save": "保存工具实验到本课笔记",
        "endpoint": "/api/playground/tool-contract",
    },
    "evaluation": {
        "lesson": "agent-reranking",
        "mode": "evaluation",
        "region": "免费检索评测",
        "run": "运行免费检索评测",
        "running": "正在评测…",
        "save": "保存评测摘要到本课笔记",
        "endpoint": "/api/playground/retrieval-evaluation",
    },
}


def base_url():
    return os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173").rstrip("/")


def tool_catalog():
    return {
        "contract_version": "tool-contract-v1",
        "tools": [
            {
                "name": name,
                "description": "Fixed public read-only test tool.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {field: {"type": "string", "minLength": 1, "maxLength": 100}},
                    "required": [field],
                },
            }
            for name, field in [("knowledge_search", "query"), ("lesson_read", "lesson_id")]
        ],
        "supported_lesson_ids": ["agent-structured-output", "agent-tool-contract"],
        "examples": [
            {
                "label": "固定搜索",
                "tool_name": "knowledge_search",
                "arguments_json": '{"query":"MCP"}',
            }
        ],
        "limits": {"arguments_bytes": 4096, "read_only": True, "model_calls": 0},
    }


@pytest.fixture
def page():
    origin = base_url()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        current = context.new_page()
        requests, errors = [], []
        current.on("request", lambda request: requests.append(request.url))
        current.on("pageerror", lambda error: errors.append(str(error)))

        def boundary(route):
            path = urlparse(route.request.url).path
            if not route.request.url.startswith(origin + "/") or path.endswith(
                ("/playground/run", "/playground/execute")
            ):
                route.abort()
            else:
                route.continue_()

        current.route("**/*", boundary)
        current.route(
            "**/api/feed*",
            lambda route: route.fulfill(
                json={
                    "items": [ARTICLE],
                    "sources": [],
                    "fetched_at": TIME,
                    "mode": "curated",
                }
            ),
        )
        current.route(
            "**/api/playground/tool-contract",
            lambda route: (
                route.fulfill(json=tool_catalog())
                if route.request.method == "GET"
                else route.abort()
            ),
        )
        try:
            yield current
            assert not errors
            assert not any(
                urlparse(url).path.endswith(("/playground/run", "/playground/execute"))
                for url in requests
            )
        finally:
            context.close()
            browser.close()


def goto(page, path):
    page.goto(base_url() + path)
    page.wait_for_load_state("networkidle")


def seed(page, value, path):
    goto(page, "/")
    put_progress(page, value)
    goto(page, path)


def external(page, value):
    # Same-document setItem deliberately withholds the other tab's storage notification.
    # The tested UI still contains its prior rendered values/closures.
    put_progress(page, value, notify=False)


def release_storage_event(page, old_payload):
    # Payload may be stale. The application must reread the actual current storage.
    page.evaluate(
        """([key, value]) => dispatchEvent(new StorageEvent('storage', {
      key, newValue: JSON.stringify(value), storageArea: localStorage
    }))""",
        [KEY, old_payload],
    )
    page.evaluate(
        """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"""
    )


def evidence(lesson="agent-research-agent", **fields):
    return {
        "lesson_id": lesson,
        "language": "python",
        "revision": "",
        "command": "",
        "success": "",
        "failure": "",
        "pending": "",
        "updated_at": TIME,
        **fields,
    }


def other_updates(value):
    latest = copy.deepcopy(value)
    latest["notes"]["fullstack-launch"] = "B 标签刚写的独立课程笔记"
    latest["evidence"] = [evidence(pending="B 标签独立证据")]
    latest["runs"] = [record(1)]
    latest["practice"] = [{"lesson_id": "fullstack-launch", "language": "go", "completed_at": TIME}]
    return latest


def assert_other_fields(before, after, *, changing):
    assert {key: value for key, value in after.items() if key not in changing} == {
        key: value for key, value in before.items() if key not in changing
    }
    assert after["history_reset_id"] == before["history_reset_id"]


@pytest.mark.parametrize("action", ["note", "language", "practice", "completion"])
def test_same_epoch_explicit_writer_preserves_unrelated_latest_fields(page, action):
    lesson = "agent-agent-loop" if action == "completion" else "fullstack-http"
    seed(page, progress(history_reset_id=EPOCH_A), "/lesson/" + lesson)
    if action == "completion":
        page.get_by_role("radio").nth(1).check()
        page.get_by_role("button", name="检查答案", exact=True).click()
        for item in (
            page.get_by_role("region", name="本课验收", exact=True).get_by_role("checkbox").all()
        ):
            item.check()
        expect(page.get_by_role("button", name="标记本课完成", exact=True)).to_be_enabled()
    elif action == "practice":
        page.get_by_role("region", name="本课实践记录", exact=True).get_by_role("checkbox").check()
    before = stored(page)
    latest = other_updates(before)
    external(page, latest)
    if action == "note":
        page.get_by_label("课程笔记", exact=True).fill("A 当前课的明确新笔记")
        assert stored(page)["notes"][lesson] == "A 当前课的明确新笔记"
        assert stored(page)["notes"]["fullstack-launch"] == latest["notes"]["fullstack-launch"]
        changed = {"notes"}
    elif action == "language":
        page.get_by_role("button", name="Go", exact=True).click()
        assert stored(page)["language"] == "go"
        changed = {"language"}
    elif action == "practice":
        page.get_by_role("button", name="标记 Python 实践完成", exact=True).click()
        expect(page.get_by_role("button", name="撤销 Python 实践记录", exact=True)).to_be_visible()
        assert latest["practice"][0] in stored(page)["practice"]
        assert len(stored(page)["practice"]) == 2
        changed = {"practice"}
    else:
        page.get_by_role("button", name="标记本课完成", exact=True).click()
        expect(page.get_by_role("button", name="取消完成标记", exact=True)).to_be_visible()
        assert stored(page)["completed"] == [lesson]
        changed = {"completed"}
    assert_other_fields(latest, stored(page), changing=changed)
    saved = stored(page)
    release_storage_event(page, before)
    assert stored(page) == saved
    page.reload()
    page.wait_for_load_state("networkidle")
    for key in ("notes", "evidence", "runs", "practice", "completed", "history_reset_id"):
        assert stored(page).get(key) == saved.get(key)


def test_bookmark_add_remove_are_explicit_and_new_add_after_reset_cannot_restore_old_data(page):
    seed(
        page,
        progress(history_reset_id=EPOCH_A, notes={"agent-mcp": OLD}, runs=[record(1)]),
        "/feed",
    )
    add = page.get_by_role("button", name="收藏：" + ARTICLE["title"], exact=True)
    remove = page.get_by_role("button", name="取消收藏：" + ARTICLE["title"], exact=True)
    expect(add).to_be_visible()
    original = stored(page)
    newest_item = {**ARTICLE, "summary": "B 保存的最新副本，不得被旧卡片覆盖"}
    latest = other_updates(original)
    latest.update(bookmarks=[ARTICLE["id"]], savedItems=[newest_item])
    external(page, latest)
    add.click()
    expect(remove).to_be_visible()
    assert stored(page) == latest
    removed = {**latest, "bookmarks": [], "savedItems": []}
    external(page, removed)
    remove.click()
    expect(add).to_be_visible()
    assert stored(page) == removed
    cleared = progress(history_reset_id=EPOCH_B)
    external(page, cleared)
    add.click()
    expect(remove).to_be_visible()
    value = stored(page)
    assert value["bookmarks"] == [ARTICLE["id"]]
    assert value["savedItems"] == [ARTICLE]
    assert_other_fields(cleared, value, changing={"bookmarks", "savedItems"})
    assert OLD not in json.dumps(value)
    release_storage_event(page, original)
    assert stored(page) == value


@pytest.mark.parametrize("editor", ["note", "evidence"])
def test_old_textarea_or_evidence_field_cannot_cross_reset_then_new_edit_works(page, editor):
    lesson = "agent-research-agent"
    seed(
        page,
        progress(
            history_reset_id=EPOCH_A,
            notes={lesson: OLD},
            runs=[record(1)],
            evidence=[evidence(command=OLD, pending=OLD)],
        ),
        "/lesson/" + lesson,
    )
    field = page.get_by_label("课程笔记" if editor == "note" else "验证命令", exact=True)
    expect(field).to_have_value(OLD)
    cleared = progress(history_reset_id=EPOCH_B)
    external(page, cleared)
    field.fill(OLD + "-stale-edit")
    expect(field).to_have_value("")
    expect(page.locator("body")).to_contain_text("未保存")
    assert stored(page) == cleared
    field.fill("新上下文明确填写")
    expect(field).to_have_value("新上下文明确填写")
    current = stored(page)
    assert OLD not in json.dumps(current) and current["history_reset_id"] == EPOCH_B
    assert not current["runs"]
    if editor == "note":
        assert current["notes"] == {lesson: "新上下文明确填写"}
        assert not current.get("evidence")
    else:
        assert len(current["evidence"]) == 1
        assert current["evidence"][0]["command"] == "新上下文明确填写"
        assert current["evidence"][0]["pending"] == ""
        assert not current["notes"]


def test_evidence_export_uses_latest_record_and_deleted_record_cannot_download(page):
    lesson = "agent-research-agent"
    seed(
        page,
        progress(history_reset_id=EPOCH_A, evidence=[evidence(command=OLD)]),
        "/lesson/" + lesson,
    )
    download_button = page.get_by_role("button", name="下载实践证据 Markdown", exact=True)
    expect(download_button).to_be_enabled()
    latest = {
        **stored(page),
        "history_reset_id": EPOCH_B,
        "evidence": [evidence(command="B 的最新导出命令", pending="B 的最新边界")],
    }
    external(page, latest)
    with page.expect_download() as event:
        download_button.click()
    markdown = Path(event.value.path()).read_text()
    assert "B 的最新导出命令" in markdown and "B 的最新边界" in markdown
    assert OLD not in markdown
    assert stored(page) == latest
    expect(page.get_by_label("验证命令", exact=True)).to_have_value("B 的最新导出命令")
    downloads = []
    page.on("download", lambda value: downloads.append(value))
    deleted = {**latest, "evidence": []}
    external(page, deleted)
    download_button.click()
    expect(download_button).to_be_disabled()
    expect(page.get_by_label("验证命令", exact=True)).to_have_value("")
    page.evaluate(
        """() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"""
    )
    assert downloads == [] and stored(page) == deleted


def region(page, mode):
    return page.get_by_role("region", name=MODES[mode]["region"], exact=True)


def open_report(page, mode):
    values = MODES[mode]
    seed(
        page,
        progress(history_reset_id=EPOCH_A),
        f"/playground?track=agent&lesson={values['lesson']}&mode={values['mode']}",
    )
    expect(region(page, mode).get_by_role("button", name=values["run"], exact=True)).to_be_enabled()
    page.evaluate("""() => {
      const original = window.fetch.bind(window);
      window.progressWriteRequests = [];
      window.fetch = (url, options) => {
        const path = new URL(String(url), location.href).pathname;
        if (options?.method === 'POST' && ['/api/playground/tool-contract', '/api/playground/retrieval-evaluation'].includes(path)) {
          return new Promise((resolve, reject) => {
            window.progressWriteRequests.push({path, input: JSON.parse(options.body), signal: options.signal, resolve, reject});
          });
        }
        return original(url, options);
      };
    }""")


def start_report(page, mode):
    index = page.evaluate("window.progressWriteRequests.length")
    region(page, mode).get_by_role("button", name=MODES[mode]["run"], exact=True).click()
    page.wait_for_function("index => window.progressWriteRequests.length === index + 1", arg=index)
    expect(
        region(page, mode).get_by_role("button", name=MODES[mode]["running"], exact=True)
    ).to_be_disabled()
    return index


def fixed_report(mode, index, request):
    run_id = f"10000000-0000-4000-8000-{index + 1:012d}"
    if mode == "tool":
        return {
            **request,
            "contract_version": "tool-contract-v1",
            "run_id": run_id,
            "model_calls": 0,
            "outcome": "success",
            "observation": {"read_only": True, "operation_id": run_id + ":1", "items": []},
        }
    empty_metrics = {
        "positive_cases": 1,
        "negative_cases": 1,
        "precision_at_k": 0,
        "recall_at_k": 0,
        "mrr": 0,
        "no_result_accuracy": 1,
    }
    cases = []
    for negative in (False, True):
        side = {
            "results": [],
            "metrics": {
                "precision_at_k": None if negative else 0,
                "recall_at_k": None if negative else 0,
                "reciprocal_rank": None if negative else 0,
                "no_result_accuracy": 1 if negative else None,
            },
        }
        cases.append(
            {
                "id": "fixed-negative" if negative else "fixed-positive",
                "query": "固定存储回归资料",
                "is_negative": negative,
                "relevant": [] if negative else [{"id": "agent-reranking", "title": "检索与重排"}],
                "baseline": copy.deepcopy(side),
                "candidate": copy.deepcopy(side),
            }
        )
    return {
        "track": "agent",
        "dataset_version": "course-retrieval-v1",
        "corpus_revision": "sha256:" + "a" * 64,
        "run_id": run_id,
        "model_calls": 0,
        "notice": "固定公开 mock；只验证写入归属。",
        "configurations": {"baseline": request["baseline"], "candidate": request["candidate"]},
        "metrics": {
            "baseline": copy.deepcopy(empty_metrics),
            "candidate": copy.deepcopy(empty_metrics),
        },
        "cases": cases,
    }


def release_report(page, mode, index, *, report=None):
    request = page.evaluate("index => window.progressWriteRequests[index].input", index)
    if report is None:
        report = fixed_report(mode, index, request)
    page.evaluate(
        """async ([index, report]) => {
      window.progressWriteRequests[index].resolve(new Response(JSON.stringify(report), {headers: {'Content-Type': 'application/json'}}));
      await new Promise(resolve => setTimeout(resolve, 0));
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    }""",
        [index, report],
    )
    return report


def completed_report(page, mode):
    index = start_report(page, mode)
    report = release_report(page, mode, index)
    expect(region(page, mode)).to_contain_text(report["run_id"])
    expect(region(page, mode).get_by_label("观察与下一步", exact=True)).to_be_visible()
    return report


def no_savable_report(page, mode):
    save = region(page, mode).get_by_role("button", name=MODES[mode]["save"], exact=True)
    if save.count():
        expect(save).to_be_disabled()
    expect(
        region(page, mode).get_by_role("button", name="已保存到本课笔记", exact=True)
    ).to_have_count(0)


@pytest.mark.parametrize("mode", MODES)
def test_free_report_save_appends_to_same_epoch_latest_note_and_preserves_other_fields(page, mode):
    open_report(page, mode)
    report = completed_report(page, mode)
    region(page, mode).get_by_label("观察与下一步", exact=True).fill("当前结果的公开反思")
    latest = other_updates(stored(page))
    lesson = MODES[mode]["lesson"]
    latest["notes"][lesson] = "B 标签刚保存的本课文本"
    external(page, latest)
    region(page, mode).get_by_role("button", name=MODES[mode]["save"], exact=True).click()
    expect(
        region(page, mode).get_by_role("button", name="已保存到本课笔记", exact=True)
    ).to_be_disabled()
    value = stored(page)
    assert_other_fields(latest, value, changing={"notes"})
    assert value["notes"]["fullstack-launch"] == latest["notes"]["fullstack-launch"]
    assert value["notes"][lesson].startswith("B 标签刚保存的本课文本\n\n")
    title = "工具契约实验记录" if mode == "tool" else "检索评测记录"
    marker = f"### {title} {report['run_id'].lower()}"
    assert value["notes"][lesson].splitlines().count(marker) == 1
    # A legal rerun may receive the same report; the saved marker must prevent another append.
    repeated = start_report(page, mode)
    release_report(page, mode, repeated, report=report)
    expect(region(page, mode)).to_contain_text(report["run_id"])
    expect(
        region(page, mode).get_by_role("button", name="已保存到本课笔记", exact=True)
    ).to_be_disabled()
    assert stored(page) == value
    assert stored(page)["notes"][lesson].splitlines().count(marker) == 1


@pytest.mark.parametrize("mode", MODES)
def test_completed_free_report_cannot_be_saved_across_hidden_reset(page, mode):
    open_report(page, mode)
    old = completed_report(page, mode)
    region(page, mode).get_by_label("观察与下一步", exact=True).fill(OLD)
    replacement = progress(history_reset_id=EPOCH_B, notes={"fullstack-launch": "新导入笔记"})
    external(page, replacement)
    region(page, mode).get_by_role("button", name=MODES[mode]["save"], exact=True).click()
    expect(page.locator("body")).to_contain_text("未保存")
    no_savable_report(page, mode)
    assert stored(page) == replacement
    current = completed_report(page, mode)
    region(page, mode).get_by_label("观察与下一步", exact=True).fill("重新运行后保存")
    region(page, mode).get_by_role("button", name=MODES[mode]["save"], exact=True).click()
    expect(
        region(page, mode).get_by_role("button", name="已保存到本课笔记", exact=True)
    ).to_be_disabled()
    note = stored(page)["notes"][MODES[mode]["lesson"]]
    assert current["run_id"] in note and old["run_id"] not in note and OLD not in note
    assert stored(page)["notes"]["fullstack-launch"] == "新导入笔记"
    assert stored(page)["history_reset_id"] == EPOCH_B


@pytest.mark.parametrize("mode", MODES)
def test_free_report_uses_start_epoch_and_ignores_aborted_late_response_during_new_run(page, mode):
    open_report(page, mode)
    old = start_report(page, mode)
    second = progress(history_reset_id=EPOCH_B)
    external(page, second)
    # No event: a report must retain the request's start epoch, not bless its arrival with B.
    old_report = release_report(page, mode, old)
    expect(
        region(page, mode).get_by_role("button", name=MODES[mode]["run"], exact=True)
    ).to_be_enabled()
    no_savable_report(page, mode)
    expect(region(page, mode)).not_to_contain_text(old_report["run_id"])
    assert stored(page) == second
    waiting = start_report(page, mode)
    third = progress(history_reset_id=EPOCH_C, notes={"fullstack-launch": "C 的记录"})
    external(page, third)
    release_storage_event(page, third)
    page.wait_for_function(
        "index => window.progressWriteRequests[index].signal.aborted", arg=waiting
    )
    expect(
        region(page, mode).get_by_role("button", name=MODES[mode]["run"], exact=True)
    ).to_be_enabled()
    current = start_report(page, mode)
    waiting_report = release_report(page, mode, waiting)
    expect(
        region(page, mode).get_by_role("button", name=MODES[mode]["running"], exact=True)
    ).to_be_disabled()
    assert not page.evaluate("index => window.progressWriteRequests[index].signal.aborted", current)
    no_savable_report(page, mode)
    final = release_report(page, mode, current)
    expect(region(page, mode)).to_contain_text(final["run_id"])
    expect(region(page, mode)).not_to_contain_text(waiting_report["run_id"])
    region(page, mode).get_by_label("观察与下一步", exact=True).fill("只保存新 epoch 的结果")
    region(page, mode).get_by_role("button", name=MODES[mode]["save"], exact=True).click()
    expect(
        region(page, mode).get_by_role("button", name="已保存到本课笔记", exact=True)
    ).to_be_disabled()
    value = stored(page)
    assert value["history_reset_id"] == EPOCH_C
    assert value["notes"]["fullstack-launch"] == "C 的记录"
    note = value["notes"][MODES[mode]["lesson"]]
    assert (
        final["run_id"] in note
        and old_report["run_id"] not in note
        and waiting_report["run_id"] not in note
    )


def test_storage_write_failure_keeps_memory_edits_across_next_writer_and_export(page):
    lesson = "fullstack-http"
    seed(
        page, progress(history_reset_id=EPOCH_A, notes={lesson: "磁盘旧笔记"}), "/lesson/" + lesson
    )
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
    page.get_by_label("课程笔记", exact=True).fill("仅在内存的新笔记")
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("仅在内存的新笔记")
    expect(page.locator("body")).to_contain_text("浏览器存储不可用")
    page.get_by_role("button", name="Go", exact=True).click()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("仅在内存的新笔记")
    backup = export_record(page)
    assert backup["notes"][lesson] == "仅在内存的新笔记"
    assert backup["language"] == "go" and backup["history_reset_id"] == EPOCH_A
    assert stored(page) == disk
