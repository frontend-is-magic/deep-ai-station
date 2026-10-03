"""Controlled SSE delivery verifies useful partial usage without real provider calls."""

import json
import os

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e

# This transport deliberately ignores AbortSignal. Pull counters are event-consumption
# barriers, so old callbacks/catch/finally can be tested without arbitrary sleeps.
STREAM_HARNESS = """
(() => {
  const original = window.fetch.bind(window);
  window.usageStreams = [];
  window.fetch = (input, options) => {
    if (String(input) !== '/api/playground/run') return original(input, options);
    const entry = { pulls: 0, cancelled: false, signal: options.signal };
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


@pytest.fixture
def page():
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


def open_lab(page):
    base = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173")
    page.goto(base + "/playground?track=agent&lesson=agent-agent-loop&workflow=agent")
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


def assert_unsuccessful(page):
    expect(page.get_by_text("运行未完成", exact=True)).to_be_visible()
    expect(page.get_by_text("实验已完成", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()
    assert runs(page) == []


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
def test_unfinished_runs_keep_known_usage_without_success_history_or_notes(page, ending):
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
    assert_unsuccessful(page)
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
    assert_unsuccessful(page)
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
    assert runs(page) == []
    emit(
        page,
        current,
        ("done", {"duration_ms": 10, "usage": {"total_tokens": 4}, "usage_complete": True}),
        ("done", {"duration_ms": 10, "usage": {"total_tokens": 4}, "usage_complete": True}),
    )
    close_stream(page, current)
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_be_enabled()
    assert len(runs(page)) == 1
    assert runs(page)[0]["id"] == f"usage-run-{current}"
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
    failed = start(page)
    emit(
        page,
        failed,
        ("error", {"message": "配额不足", "usage": {"total_tokens": 99}, "usage_complete": True}),
    )
    close_stream(page, failed)
    expect(usage(page)).to_contain_text("总量 99")
    assert len(runs(page)) == 1
    page.get_by_role("button", name="运行历史", exact=False).click()
    page.locator(".run-history button").first.click()
    expect(usage(page)).to_contain_text("tokens · 输入 0 / 输出 3")
    expect(usage(page)).not_to_contain_text("总量 99")
    expect(page.get_by_role("alert")).to_have_count(0)
    page.get_by_label("学习方向", exact=True).select_option("fullstack")
    expect(usage(page)).to_have_count(0)
    expect(page.get_by_text("实验已完成", exact=True)).to_have_count(0)
    page.reload()
    page.get_by_role("button", name="运行历史", exact=False).click()
    page.locator(".run-history button").first.click()
    expect(usage(page)).to_contain_text("tokens · 输入 0 / 输出 3")
    assert len(runs(page)) == 1
