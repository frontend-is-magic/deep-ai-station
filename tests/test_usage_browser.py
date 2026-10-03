"""Controlled SSE delivery verifies useful partial usage without real provider calls."""

import json
import os
from contextlib import contextmanager
from uuid import UUID

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e

# This transport deliberately ignores AbortSignal. Pull counters are event-consumption
# barriers, so old callbacks/catch/finally can be tested without arbitrary sleeps.
STREAM_HARNESS = """
(() => {
  const original = window.fetch.bind(window);
  window.usageStreams = [];
  window.usageResponses = [];
  window.usageCalls = [];
  window.fetch = (input, options) => {
    if (String(input) !== '/api/playground/run') return original(input, options);
    const entry = { pulls: 0, cancelled: false, signal: options.signal, request: JSON.parse(options.body) };
    window.usageCalls.push(entry.request);
    const response = window.usageResponses.shift();
    if (response) return Promise.resolve(new Response(JSON.stringify(response.body), {
      status: response.status, headers: { 'Content-Type': 'application/json' },
    }));
    const stream = new ReadableStream({
      start(controller) { entry.controller = controller; },
      pull() { entry.pulls += 1; },
      cancel() { entry.cancelled = true; },
    }, { highWaterMark: 0 });
    window.usageStreams.push(entry);
    return Promise.resolve(new Response(stream, {
      status: 200, headers: { 'Content-Type': 'text/event-stream' },
    }));
  };
})();
"""


@contextmanager
def usage_page_context():
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        current = context.new_page()
        current.on("pageerror", lambda error: errors.append(str(error)))
        current.add_init_script(STREAM_HARNESS)
        current.route(
            "**/api/capabilities",
            lambda route: route.fulfill(
                json={
                    "providers": [
                        {
                            "id": "deepseek",
                            "name": "DeepSeek",
                            "enabled": True,
                            "model": "test-model",
                        }
                    ],
                    "sandbox": {"languages": []},
                }
            ),
        )
        try:
            yield current
        finally:
            context.close()
            browser.close()
            assert not errors, errors


@pytest.fixture
def page():
    with usage_page_context() as current:
        yield current


def open_lab(page, *, track="agent", lesson_id="agent-agent-loop", workflow="agent"):
    base = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173")
    page.goto(base + f"/playground?track={track}&lesson={lesson_id}&workflow={workflow}")
    expect(page.get_by_role("heading", name="Playground", exact=True)).to_be_visible()
    page.get_by_label("模型服务", exact=True).select_option("deepseek")
    page.get_by_text("高级配置", exact=True).click()
    page.get_by_label("实验访问码", exact=True).fill("test-only-usage-code")


def usage(page):
    return page.get_by_role("region", name="模型用量", exact=True)


def emit(page, index, *events):
    body = "".join(
        f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n" for name, data in events
    )
    previous = page.evaluate(
        """([index, body]) => {
        const entry = window.usageStreams[index];
        const previous = entry.pulls;
        entry.controller.enqueue(new TextEncoder().encode(body));
        return previous;
    }""",
        [index, body],
    )
    page.wait_for_function(
        "([index, previous]) => window.usageStreams[index].pulls > previous || window.usageStreams[index].cancelled",
        arg=[index, previous],
    )


def start(page):
    index = page.evaluate("window.usageStreams.length")
    page.get_by_role("button", name="运行实验", exact=True).click()
    page.wait_for_function(
        "index => window.usageStreams.length > index && window.usageStreams[index].pulls > 0",
        arg=index,
    )
    emit(page, index, ("start", {"run_id": f"usage-run-{index}"}))
    return index


def close_stream(page, index):
    page.evaluate("index => window.usageStreams[index].controller.close()", index)


def runs(page):
    return page.evaluate(
        "JSON.parse(localStorage.getItem('deep-ai-station:v1') || '{}').runs || []"
    )


def assert_unsuccessful(page, *, status="failed", reason="server_error", count=1):
    result = page.get_by_role("region", name="运行结果", exact=True)
    expect(result).to_have_attribute("data-run-status", status)
    expect(
        page.get_by_text("客户端已停止" if status == "cancelled" else "运行失败", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("实验已完成", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()
    page.wait_for_function(
        "count => (JSON.parse(localStorage.getItem('deep-ai-station:v1') || '{}').runs || []).length === count",
        arg=count,
    )
    record = runs(page)[0]
    assert record["status"] == status and record["reason"] == reason
    assert str(UUID(record["id"])) == record["id"] and UUID(record["id"]).version == 4
    expect(result).to_have_attribute("data-run-id", record["id"])
    return record


def test_usage_snapshots_replace_duplicates_and_distinguish_zero_from_missing(page):
    open_lab(page)
    index = start(page)
    expect(usage(page)).to_contain_text("用量未知")
    snapshot = {
        "run_id": f"usage-run-{index}",
        "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
        "usage_complete": True,
    }
    emit(page, index, ("usage", snapshot), ("usage", snapshot))
    expect(usage(page)).to_contain_text("tokens · 输入 7 / 输出 5 / 总量 12")
    expect(usage(page)).to_contain_text("运行尚未结束")
    emit(page, index, ("usage", {"usage": {"total_tokens": 18}, "usage_complete": False}))
    expect(usage(page)).to_contain_text("tokens · 总量 18")
    expect(usage(page)).not_to_contain_text("输入")
    emit(page, index, ("usage", {"usage": None, "usage_complete": False}))
    expect(usage(page)).to_contain_text("用量未知")
    expect(usage(page)).not_to_contain_text("tokens")
    emit(page, index, ("usage", {"usage": {"total_tokens": 0}, "usage_complete": False}))
    expect(usage(page)).to_contain_text("tokens · 总量 0")
    emit(
        page,
        index,
        ("error", {"message": "本轮未完成"}),
        ("done", {"duration_ms": 10, "usage": {"total_tokens": 999}, "usage_complete": True}),
    )
    expect(usage(page)).to_contain_text("tokens · 总量 0")
    expect(usage(page)).to_contain_text("统计不完整")
    assert_unsuccessful(page)
    close_stream(page, index)


@pytest.mark.parametrize(
    "ending", ["provider-error", "quota-error", "truncated", "eof", "transport-error"]
)
def test_unfinished_runs_keep_known_usage_in_failed_history_without_note_eligibility(page, ending):
    open_lab(page)
    index = start(page)
    emit(
        page,
        index,
        ("usage", {"usage": {"total_tokens": 12}, "usage_complete": False}),
        ("delta", {"text": "部分回答"}),
    )
    if ending in {"provider-error", "quota-error"}:
        emit(
            page,
            index,
            (
                "error",
                {
                    "message": "供应商失败" if ending == "provider-error" else "下一轮配额不足",
                    "usage": {"total_tokens": 12},
                    "usage_complete": ending == "quota-error",
                },
            ),
        )
        close_stream(page, index)
    elif ending == "truncated":
        emit(
            page,
            index,
            (
                "done",
                {
                    "duration_ms": 10,
                    "truncated": True,
                    "usage": {"total_tokens": 12},
                    "usage_complete": True,
                },
            ),
        )
        close_stream(page, index)
    elif ending == "eof":
        close_stream(page, index)
    else:
        page.evaluate(
            "index => window.usageStreams[index].controller.error(new Error('mock transport ended'))",
            index,
        )
    reason = {
        "truncated": "output_limit",
        "eof": "stream_ended",
        "transport-error": "transport_error",
    }.get(ending, "server_error")
    record = assert_unsuccessful(page, reason=reason)
    assert record["usage"] == {"total_tokens": 12}
    assert record["usage_complete"] is (ending in {"quota-error", "truncated"})
    assert record["answer"] == "部分回答"
    expect(page.locator(".markdown-output")).to_contain_text("部分回答")
    expect(usage(page)).to_contain_text("tokens · 总量 12")
    expect(usage(page)).to_contain_text(
        "已发出请求均已统计" if ending in {"quota-error", "truncated"} else "统计不完整"
    )
    page.set_viewport_size({"width": 375, "height": 812})
    expect(usage(page)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_stop_is_immediate_and_old_ignored_abort_stream_cannot_corrupt_a_new_run(page):
    open_lab(page)
    old = start(page)
    emit(page, old, ("usage", {"usage": {"total_tokens": 9}, "usage_complete": True}))
    page.get_by_role("button", name="停止运行", exact=True).click()
    expect(usage(page)).to_contain_text("tokens · 总量 9")
    expect(usage(page)).to_contain_text("统计不完整")
    old_record = assert_unsuccessful(page, status="cancelled", reason="user_stop")
    assert old_record["usage"] == {"total_tokens": 9}
    assert old_record["usage_complete"] is False
    assert page.evaluate("index => window.usageStreams[index].signal.aborted", old)
    current = start(page)
    expect(usage(page)).to_contain_text("用量未知")
    expect(usage(page)).not_to_contain_text("总量 9")
    emit(
        page,
        old,
        ("usage", {"usage": {"total_tokens": 999}}),
        ("delta", {"text": "迟到旧回答"}),
        ("error", {"message": "迟到旧错误"}),
        ("done", {"duration_ms": 1, "usage": {"total_tokens": 999}}),
    )
    close_stream(page, old)
    emit(page, current, ("usage", {"usage": {"total_tokens": 4}}), ("delta", {"text": "新回答"}))
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_be_visible()
    expect(page.get_by_role("alert")).to_have_count(0)
    expect(usage(page)).to_contain_text("tokens · 总量 4")
    expect(page.locator(".markdown-output")).to_have_text("新回答")
    assert runs(page) == [old_record]
    emit(
        page,
        current,
        ("done", {"duration_ms": 10, "usage": {"total_tokens": 4}, "usage_complete": True}),
        ("done", {"duration_ms": 10, "usage": {"total_tokens": 4}, "usage_complete": True}),
    )
    close_stream(page, current)
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_be_enabled()
    assert len(runs(page)) == 2
    assert runs(page)[0]["status"] == "completed"
    assert "reason" not in runs(page)[0]
    assert runs(page)[0]["server_run_id"] == f"usage-run-{current}"
    assert runs(page)[0]["id"] != f"usage-run-{current}"
    assert runs(page)[1] == old_record
    assert runs(page)[0]["usage"] == {"total_tokens": 4}


def test_legacy_done_history_replay_and_track_changes_do_not_reuse_failed_live_usage(page):
    open_lab(page)
    successful = start(page)
    emit(
        page,
        successful,
        ("delta", {"text": "旧协议成功回答"}),
        ("done", {"duration_ms": 10, "usage": {"input_tokens": 0, "output_tokens": 3}}),
    )
    close_stream(page, successful)
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    expect(usage(page)).to_contain_text("供应商已返回 usage")
    expect(usage(page)).to_contain_text("tokens · 输入 0 / 输出 3")
    assert len(runs(page)) == 1
    successful_id = runs(page)[0]["id"]
    failed = start(page)
    emit(
        page,
        failed,
        ("error", {"message": "配额不足", "usage": {"total_tokens": 99}, "usage_complete": True}),
    )
    close_stream(page, failed)
    expect(usage(page)).to_contain_text("总量 99")
    assert len(runs(page)) == 2
    page.get_by_role("button", name="运行历史", exact=False).click()
    page.locator(f'.run-history button[data-run-id="{successful_id}"]').click()
    expect(usage(page)).to_contain_text("tokens · 输入 0 / 输出 3")
    expect(usage(page)).not_to_contain_text("总量 99")
    expect(page.get_by_role("alert")).to_have_count(0)
    page.get_by_label("学习方向", exact=True).select_option("fullstack")
    expect(usage(page)).to_have_count(0)
    expect(page.get_by_text("实验已完成", exact=True)).to_have_count(0)
    page.reload()
    page.get_by_role("button", name="运行历史", exact=False).click()
    page.locator(f'.run-history button[data-run-id="{successful_id}"]').click()
    expect(usage(page)).to_contain_text("tokens · 输入 0 / 输出 3")
    assert len(runs(page)) == 2
