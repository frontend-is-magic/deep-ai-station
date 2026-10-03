"""Ended attempts persist once; replay/reset never launches a provider request."""

import json
import re
from pathlib import Path
from uuid import UUID

import pytest
from playwright.sync_api import expect
from test_usage_browser import (
    assert_unsuccessful,
    close_stream,
    emit,
    open_lab,
    runs,
    start,
    usage,
    usage_page_context,
)

pytestmark = pytest.mark.e2e
STORAGE_KEY = "deep-ai-station:v1"
DIAGNOSTIC = "private-history-diagnostic-fixture"


@pytest.fixture
def page():
    with usage_page_context() as current:
        yield current


def stored(page):
    return page.evaluate("key => JSON.parse(localStorage.getItem(key) || '{}')", STORAGE_KEY)


def progress(**values):
    return {
        "version": 1,
        "completed": [],
        "bookmarks": [],
        "notes": {},
        "language": "python",
        "runs": [],
        **values,
    }


def put_progress(page, value, *, notify=True):
    page.evaluate(
        """([key, value, notify]) => {
          localStorage.setItem(key, JSON.stringify(value));
          if (notify) dispatchEvent(new StorageEvent('storage', {
            key, newValue: JSON.stringify(value), storageArea: localStorage
          }));
        }""",
        [STORAGE_KEY, value, notify],
    )


def record(number, **values):
    return {
        "id": f"00000000-0000-4000-8000-{number:012d}",
        "status": "completed",
        "prompt": f"已有运行 {number}",
        "answer": f"已有回答 {number}",
        "provider": "deepseek",
        "track": "agent",
        "lesson_id": "agent-agent-loop",
        "workflow": "agent",
        "date": f"2026-10-01T00:00:{number:02d}.000Z",
        "duration_ms": 10,
        **values,
    }


def settings(page):
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    return page.get_by_role("dialog", name="你的学习空间", exact=True)


def export_record(page):
    dialog = settings(page)
    with page.expect_download() as download:
        dialog.get_by_role("button", name="导出学习记录", exact=True).click()
    result = json.loads(Path(download.value.path()).read_text())
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    return result


def import_record(page, value):
    dialog = settings(page)
    dialog.locator('input[type="file"]').set_input_files(
        {
            "name": "public-history-fixture.json",
            "mimeType": "application/json",
            "buffer": json.dumps(value, ensure_ascii=False).encode(),
        }
    )
    return dialog


def restore(page, run_id):
    page.get_by_role("button", name="运行历史", exact=False).click()
    history = page.get_by_role("region", name="实验历史", exact=True)
    history.locator(f'button[data-run-id="{run_id}"]').click()
    result = page.get_by_role("region", name="运行结果", exact=True)
    expect(result).to_have_attribute("data-run-id", run_id)
    return result


def begin_without_start_event(page):
    index = page.evaluate("window.usageStreams.length")
    page.get_by_role("button", name="运行实验", exact=True).click()
    page.wait_for_function(
        "index => window.usageStreams.length > index && window.usageStreams[index].pulls > 0",
        arg=index,
    )
    return index


@pytest.mark.parametrize(
    ("track", "lesson_id", "workflow"),
    [("agent", "agent-agent-loop", "agent"), ("fullstack", "fullstack-http", "retrieval")],
)
def test_failed_snapshots_refresh_and_backup_replay_without_requests(
    page, track, lesson_id, workflow
):
    open_lab(page, track=track, lesson_id=lesson_id, workflow=workflow)
    baseline = progress(
        notes={lesson_id: "原笔记"},
        completed=[lesson_id],
        practice=[
            {
                "lesson_id": lesson_id,
                "language": "python",
                "completed_at": "2026-10-01T00:00:00.000Z",
            }
        ],
    )
    put_progress(page, baseline)
    page.get_by_label("任务描述", exact=True).fill("保留失败时的公开观察")
    page.get_by_label("系统提示", exact=True).fill(DIAGNOSTIC + "-system")
    page.get_by_label("实验访问码", exact=True).fill(DIAGNOSTIC + "-access")
    index = start(page)
    emit(
        page,
        index,
        (
            "trace",
            {"id": "request-1", "title": "固定检索", "detail": "等待资料", "status": "running"},
        ),
        (
            "trace",
            {
                "id": "request-1",
                "title": "固定检索",
                "detail": "已读取公开资料",
                "status": "success",
                "debug": DIAGNOSTIC,
            },
        ),
        ("delta", {"text": "可回看的片段"}),
        (
            "usage",
            {
                "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2},
                "usage_complete": False,
            },
        ),
        (
            "error",
            {
                "message": DIAGNOSTIC,
                "usage": {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2},
                "usage_complete": True,
                "headers": {"Authorization": DIAGNOSTIC},
            },
        ),
        ("done", {"duration_ms": 999, "usage": {"total_tokens": 999}, "usage_complete": True}),
    )
    saved = assert_unsuccessful(page)
    close_stream(page, index)
    assert saved["server_run_id"] == f"usage-run-{index}"
    assert saved["id"] != saved["server_run_id"]
    assert saved["track"] == track and saved["lesson_id"] == lesson_id
    assert saved["workflow"] == workflow and saved["usage_complete"] is True
    assert saved["usage"] == {"prompt_tokens": 0, "completion_tokens": 2, "total_tokens": 2}
    assert saved["trace"] == [
        {"id": "request-1", "title": "固定检索", "detail": "已读取公开资料", "status": "success"}
    ]
    assert saved["answer"] == "可回看的片段"
    assert DIAGNOSTIC not in json.dumps(stored(page))
    for key in ("notes", "completed", "practice"):
        assert stored(page)[key] == baseline[key]
    backup = export_record(page)
    assert DIAGNOSTIC not in json.dumps(backup)
    assert backup["runs"] == [saved]
    page.reload()
    expect(page.get_by_role("heading", name="Playground", exact=True)).to_be_visible()
    result = restore(page, saved["id"])
    expect(result).to_have_attribute("data-run-status", "failed")
    expect(result).to_contain_text("已接收片段")
    expect(result).not_to_contain_text(DIAGNOSTIC)
    expect(result.locator(".animate-spin")).to_have_count(0)
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
    assert page.evaluate("window.usageCalls.length") == 0
    dialog = import_record(page, backup)
    expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    restore(page, saved["id"])
    assert stored(page)["runs"] == backup["runs"]
    assert page.evaluate("window.usageCalls.length") == 0
    page.set_viewport_size({"width": 375, "height": 812})
    expect(result).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("counts", [None, {"total_tokens": 0}, {"prompt_tokens": 4}])
def test_stop_saves_null_zero_and_partial_counts_without_claiming_server_completion(page, counts):
    open_lab(page)
    index = start(page)
    emit(page, index, ("usage", {"usage": counts, "usage_complete": True}))
    page.get_by_role("button", name="停止运行", exact=True).click()
    saved = assert_unsuccessful(page, status="cancelled", reason="user_stop")
    assert saved["usage"] == counts and saved["usage_complete"] is False
    expect(page.get_by_role("region", name="运行结果", exact=True)).to_contain_text(
        "服务器终态未确认"
    )
    close_stream(page, index)
    page.reload()
    expect(page.get_by_role("heading", name="Playground", exact=True)).to_be_visible()
    result = restore(page, saved["id"])
    expect(result).to_have_attribute("data-run-status", "cancelled")
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
    if counts is not None:
        expect(usage(page)).to_contain_text("总量 0" if "total_tokens" in counts else "输入 4")
    assert runs(page) == [saved]
    assert page.evaluate("window.usageCalls.length") == 0


@pytest.mark.parametrize("ending", ["http", "stop"])
def test_prestart_attempt_has_only_local_identity_and_never_persists_diagnostics(page, ending):
    open_lab(page)
    if ending == "http":
        page.evaluate(
            "secret => window.usageResponses.push({status: 429, body: {detail: secret, token: secret}})",
            DIAGNOSTIC,
        )
        page.get_by_role("button", name="运行实验", exact=True).click()
        saved = assert_unsuccessful(page, reason="transport_error")
    else:
        index = begin_without_start_event(page)
        page.get_by_role("button", name="停止运行", exact=True).click()
        saved = assert_unsuccessful(page, status="cancelled", reason="user_stop")
        close_stream(page, index)
    assert UUID(saved["id"]).version == 4
    assert "server_run_id" not in saved
    assert saved["trace"] == [] and saved["usage"] is None and saved["usage_complete"] is False
    assert "steps" not in saved and "tool_count" not in saved
    expect(page.get_by_role("region", name="运行结果", exact=True)).to_contain_text(
        "未取得服务端运行 ID"
    )
    assert DIAGNOSTIC not in json.dumps(stored(page))
    assert DIAGNOSTIC not in json.dumps(export_record(page))


@pytest.mark.parametrize("destination", ["lesson", "library"])
def test_navigation_saves_old_course_once_and_late_frames_cannot_cross_context(page, destination):
    open_lab(page)
    index = start(page)
    emit(
        page,
        index,
        ("delta", {"text": "旧课已接收片段"}),
        ("usage", {"usage": {"total_tokens": 3}}),
    )
    if destination == "lesson":
        page.evaluate("""() => {
          const url = new URL(location.href); url.searchParams.set('lesson', 'agent-mcp');
          history.pushState({}, '', url); dispatchEvent(new PopStateEvent('popstate'));
        }""")
        expect(page.locator(".linked-lesson a")).to_have_attribute("href", "/lesson/agent-mcp")
    else:
        page.get_by_role("navigation", name="主导航", exact=True).get_by_role(
            "link", name="我的学习库", exact=True
        ).click()
        expect(page).to_have_url(re.compile(r"/library$"))
    page.wait_for_function("index => window.usageStreams[index].signal.aborted", arg=index)
    page.wait_for_function(
        "JSON.parse(localStorage.getItem('deep-ai-station:v1')).runs.length === 1"
    )
    saved = runs(page)[0]
    assert saved["status"] == "cancelled" and saved["reason"] == "context_changed"
    assert saved["lesson_id"] == "agent-agent-loop" and saved["track"] == "agent"
    assert saved["answer"] == "旧课已接收片段" and saved["usage_complete"] is False
    emit(
        page,
        index,
        ("delta", {"text": "迟到旧课文字"}),
        ("done", {"duration_ms": 1, "usage": {"total_tokens": 999}}),
    )
    close_stream(page, index)
    assert runs(page) == [saved]
    assert not stored(page).get("notes") and not stored(page).get("practice")
    if destination == "lesson":
        expect(page.locator(".markdown-output")).to_have_count(0)
        expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)


def test_twenty_entry_cap_is_shared_and_duplicate_terminal_does_not_evict_twice(page):
    open_lab(page)
    original = [record(index) for index in range(20, 0, -1)]
    original[1].update(status="failed", reason="server_error")
    original[2].update(status="cancelled", reason="user_stop", usage_complete=False)
    put_progress(page, progress(runs=original))
    index = start(page)
    emit(
        page,
        index,
        ("error", {"message": "预期失败"}),
        ("error", {"message": "重复失败"}),
        ("done", {"duration_ms": 1}),
    )
    saved = assert_unsuccessful(page, count=20)
    close_stream(page, index)
    assert runs(page) == [saved, *original[:19]]
    assert original[-1]["id"] not in {item["id"] for item in runs(page)}
    assert len({item["id"] for item in runs(page)}) == 20


def test_finalize_reads_latest_storage_even_before_storage_event_delivery(page):
    open_lab(page)
    initial = progress(notes={"agent-mcp": "旧笔记"})
    put_progress(page, initial)
    index = start(page)
    latest = progress(
        notes={"agent-mcp": "另一标签刚保存的笔记"},
        completed=["agent-mcp"],
        runs=[record(1)],
        evidence=[
            {
                "lesson_id": "agent-mcp",
                "language": "python",
                "revision": "public-rev",
                "command": "uv run pytest",
                "success": "成功证据",
                "failure": "失败证据",
                "pending": "待验证",
                "updated_at": "2026-10-01T00:00:00.000Z",
            }
        ],
    )
    put_progress(page, latest, notify=False)
    emit(page, index, ("error", {"message": "固定错误"}))
    saved = assert_unsuccessful(page, count=2)
    close_stream(page, index)
    result = stored(page)
    assert result["runs"] == [saved, *latest["runs"]]
    for key in ("notes", "completed", "evidence"):
        assert result[key] == latest[key]
    page.evaluate(
        "key => dispatchEvent(new StorageEvent('storage', {key, newValue: '{}', storageArea: localStorage}))",
        STORAGE_KEY,
    )
    assert export_record(page)["runs"] == result["runs"]


@pytest.mark.parametrize("reset", ["import", "clear"])
def test_active_import_or_clear_discards_attempt_and_late_frames_cannot_resurrect_it(page, reset):
    open_lab(page)
    old_epoch = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    put_progress(page, progress(notes={"agent-agent-loop": "旧笔记"}, history_reset_id=old_epoch))
    index = start(page)
    emit(
        page,
        index,
        ("delta", {"text": "必须随重置丢弃的片段"}),
        ("usage", {"usage": {"total_tokens": 9}}),
    )
    if reset == "import":
        imported = progress(notes={"agent-mcp": "导入笔记"}, runs=[record(1)])
        dialog = import_record(page, imported)
        expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
    else:
        dialog = settings(page)
        dialog.get_by_text("清空当前设备记录", exact=True).click()
        dialog.get_by_role("button", name="确认清空", exact=True).click()
        expect(dialog.get_by_role("status")).to_contain_text("当前设备学习记录已清空")
    page.wait_for_function("index => window.usageStreams[index].signal.aborted", arg=index)
    reset_value = stored(page)
    assert reset_value["history_reset_id"] != old_epoch
    assert UUID(reset_value["history_reset_id"]).version == 4
    assert "必须随重置丢弃的片段" not in json.dumps(reset_value, ensure_ascii=False)
    assert reset_value["notes"] == ({"agent-mcp": "导入笔记"} if reset == "import" else {})
    assert reset_value["runs"] == ([record(1)] if reset == "import" else [])
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    emit(page, index, ("delta", {"text": "迟到不能复活"}), ("done", {"duration_ms": 1}))
    close_stream(page, index)
    assert stored(page) == reset_value
    expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)


def test_rejected_import_does_not_cancel_current_attempt_or_rotate_epoch(page):
    open_lab(page)
    epoch = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    put_progress(page, progress(history_reset_id=epoch))
    index = start(page)
    dialog = import_record(page, {"version": 999})
    expect(dialog.get_by_role("status")).to_contain_text("学习记录格式不正确")
    assert stored(page)["history_reset_id"] == epoch
    assert not page.evaluate("index => window.usageStreams[index].signal.aborted", index)
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    emit(
        page,
        index,
        ("done", {"duration_ms": 1, "usage": {"total_tokens": 0}, "usage_complete": True}),
    )
    close_stream(page, index)
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    assert len(runs(page)) == 1 and runs(page)[0]["status"] == "completed"


def test_failed_record_remains_exportable_when_storage_write_fails(page):
    page.add_init_script(
        "Storage.prototype.setItem = function () { throw new DOMException('fixture quota', 'QuotaExceededError'); };"
    )
    open_lab(page)
    index = start(page)
    emit(page, index, ("delta", {"text": "内存中的失败片段"}), ("error", {"message": "固定失败"}))
    close_stream(page, index)
    result = page.get_by_role("region", name="运行结果", exact=True)
    expect(result).to_have_attribute("data-run-status", "failed")
    expect(page.locator(".storage-notice")).to_contain_text("本次记录暂存在内存")
    assert runs(page) == []
    backup = export_record(page)
    assert len(backup["runs"]) == 1
    saved = backup["runs"][0]
    assert saved["answer"] == "内存中的失败片段" and saved["status"] == "failed"
    restore(page, saved["id"])
    expect(result).to_have_attribute("data-run-id", saved["id"])
    assert page.evaluate("window.usageCalls.length") == 1


def test_legacy_success_and_unknown_lesson_failure_replay_without_running(page):
    open_lab(page)
    legacy = record(1, id="legacy-run-id", provider="openai", prompt="旧备份成功记录")
    legacy.pop("status")
    legacy.pop("workflow")
    unknown = record(
        2,
        status="failed",
        reason="server_error",
        lesson_id="agent-removed-fixture",
        prompt="失效课时失败记录",
    )
    failed = record(3, status="failed", reason="server_error", lesson_id="agent-mcp")
    dialog = import_record(page, progress(runs=[unknown, failed, legacy]))
    expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    result = restore(page, legacy["id"])
    expect(result).to_have_attribute("data-run-status", "completed")
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_be_enabled()
    restore(page, failed["id"])
    expect(page.locator(".linked-lesson a")).to_have_attribute("href", "/lesson/agent-mcp")
    expect(result).to_have_attribute("data-run-status", "failed")
    expect(result.get_by_role("alert")).not_to_be_empty()
    expect(result.get_by_role("alert")).to_have_text("运行失败，已保留已接收的内容与用量。")
    restore(page, unknown["id"])
    expect(result).to_have_attribute("data-run-status", "failed")
    expect(page.locator('a[href="/lesson/agent-removed-fixture"]')).to_have_count(0)
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
    assert "lesson=" not in page.url
    assert page.evaluate("window.usageCalls.length") == 0
    assert export_record(page)["runs"] == [unknown, failed, legacy]
    # The old successful button remains rendered while another tab resets storage.
    # Clicking it must re-read storage, not bind the deleted result to the new epoch.
    page.get_by_role("button", name="运行历史", exact=False).click()
    stale_button = page.get_by_role("region", name="实验历史", exact=True).locator(
        f'button[data-run-id="{legacy["id"]}"]'
    )
    expect(stale_button).to_be_visible()
    cleared = progress(history_reset_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    put_progress(page, cleared, notify=False)
    stale_button.click()
    expect(page.locator(".markdown-output")).to_have_count(0)
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
    assert stored(page) == cleared
    assert page.evaluate("window.usageCalls.length") == 0
    # A fresh attempt after this reset must inherit the stored epoch immediately.
    index = start(page)
    emit(page, index, ("delta", {"text": "清空后新尝试的片段"}))
    expect(page.locator(".markdown-output")).to_have_text("清空后新尝试的片段")
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_be_visible()
    assert not page.evaluate("index => window.usageStreams[index].signal.aborted", index)
    assert runs(page) == []
    emit(page, index, ("done", {"duration_ms": 1}))
    close_stream(page, index)
    expect(result).to_have_attribute("data-run-status", "completed")
    saved = runs(page)
    assert len(saved) == 1 and saved[0]["answer"] == "清空后新尝试的片段"
    assert saved[0]["server_run_id"] == f"usage-run-{index}"
    assert saved[0]["id"] not in {legacy["id"], failed["id"], unknown["id"]}
    assert stored(page)["history_reset_id"] == cleared["history_reset_id"]


def test_new_epoch_language_change_does_not_cancel_fullstack_agent_attempt(page):
    open_lab(page, track="fullstack", lesson_id="fullstack-http", workflow="agent")
    previous = progress(
        language="typescript", history_reset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    )
    put_progress(page, previous)
    # Confirm React has consumed the previous preference before the external import.
    page.get_by_role("button", name="代码实验", exact=True).click()
    expect(page.get_by_label("代码语言", exact=True)).to_have_value("typescript")
    page.get_by_role("button", name="Agent 工作流", exact=True).click()
    imported = progress(language="go", history_reset_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    put_progress(page, imported, notify=False)
    index = start(page)
    emit(page, index, ("delta", {"text": "新偏好不会取消模型观察"}))
    expect(page.locator(".markdown-output")).to_have_text("新偏好不会取消模型观察")
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_be_visible()
    assert not page.evaluate("index => window.usageStreams[index].signal.aborted", index)
    assert runs(page) == []
    emit(page, index, ("done", {"duration_ms": 1}))
    close_stream(page, index)
    expect(page.get_by_role("region", name="运行结果", exact=True)).to_have_attribute(
        "data-run-status", "completed"
    )
    saved = runs(page)
    assert len(saved) == 1 and saved[0]["status"] == "completed"
    assert saved[0]["track"] == "fullstack" and saved[0]["lesson_id"] == "fullstack-http"
    assert saved[0]["workflow"] == "agent"
    assert saved[0]["server_run_id"] == f"usage-run-{index}"
    assert stored(page)["history_reset_id"] == imported["history_reset_id"]
    assert stored(page)["language"] == "go"
    page.get_by_role("button", name="代码实验", exact=True).click()
    expect(page.get_by_label("代码语言", exact=True)).to_have_value("go")


def test_stale_history_button_uses_latest_record_and_survives_delayed_storage_event(page):
    open_lab(page)
    original = record(1, prompt="旧版本任务", answer="旧版本回答")
    previous = progress(runs=[original], history_reset_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    put_progress(page, previous)
    page.get_by_role("button", name="运行历史", exact=False).click()
    stale_button = page.get_by_role("region", name="实验历史", exact=True).locator(
        f'button[data-run-id="{original["id"]}"]'
    )
    expect(stale_button).to_contain_text("旧版本任务")
    replacement = {**original, "prompt": "导入的新任务", "answer": "导入的新回答"}
    imported = progress(
        runs=[replacement],
        notes={"agent-agent-loop": "保留导入的笔记"},
        history_reset_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    put_progress(page, imported, notify=False)
    stale_button.click()
    result = page.get_by_role("region", name="运行结果", exact=True)
    expect(result).to_have_attribute("data-run-id", replacement["id"])
    expect(page.get_by_label("任务描述", exact=True)).to_have_value("导入的新任务")
    expect(page.locator(".markdown-output")).to_have_text("导入的新回答")
    # Even a queued old payload must trigger a read of the current stored record.
    page.evaluate(
        """([key, oldValue]) => dispatchEvent(new StorageEvent('storage', {
          key, newValue: JSON.stringify(oldValue), storageArea: localStorage
        }))""",
        [STORAGE_KEY, previous],
    )
    expect(result).to_have_attribute("data-run-status", "completed")
    expect(page.locator(".markdown-output")).to_have_text("导入的新回答")
    note = page.get_by_role("button", name="写入本课笔记", exact=True)
    expect(note).to_be_enabled()
    note.click()
    expect(page.get_by_role("button", name="已写入本课笔记", exact=True)).to_be_disabled()
    saved = stored(page)
    assert saved["runs"] == [replacement]
    assert saved["history_reset_id"] == imported["history_reset_id"]
    assert saved["notes"]["agent-agent-loop"].startswith("保留导入的笔记")
    assert "导入的新任务" in saved["notes"]["agent-agent-loop"]
    assert "导入的新回答" in saved["notes"]["agent-agent-loop"]
    assert "旧版本" not in saved["notes"]["agent-agent-loop"]
    assert page.evaluate("window.usageCalls.length") == 0
