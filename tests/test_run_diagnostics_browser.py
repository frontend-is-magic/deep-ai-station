"""Real free diagnostic runs and learning actions; controlled transport only for races.

The shared E2E runner owns servers. No provider, sandbox, or external URL is used.
"""

import copy
import json
import math
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import pytest
from playwright.sync_api import expect
from test_progress_write_browser import (
    EPOCH_A,
    EPOCH_B,
    base_url,
    external,
    goto,
    release_storage_event,
)
from test_progress_write_browser import page as page
from test_run_history_browser import (
    export_record,
    import_record,
    progress,
    put_progress,
    record,
    stored,
)

pytestmark = pytest.mark.e2e
ENDPOINT = "/api/playground/diagnostics"
KEY = "deep-ai-station:v1"
LESSONS = {"agent": "agent-tracing", "fullstack": "fullstack-observability"}
RUN = "运行诊断演练"
OBSERVATION = "我的观察与下一步"
PREVIEW = "预览诊断笔记"
SAVE = "追加诊断到本课笔记"
SAVED = "已追加到本课笔记"
REFLECTION = "我的判断需要逐阶段验证。\n````\n<script>window.diagnosticsSentinel=1</script>\n下一步：核对真实 dispatch 与跳过阶段。😀"


def url(track="agent"):
    return f"/playground?track={track}&lesson={LESSONS[track]}&mode=diagnostics"


def lab(page):
    return page.get_by_role("region", name="免费运行诊断演练", exact=True)


def report_region(page):
    return page.get_by_role("region", name="实际运行报告", exact=True)


def baseline(**values):
    return progress(
        **{
            "history_reset_id": EPOCH_A,
            "language": "go",
            "notes": {LESSONS["agent"]: "原追踪笔记", LESSONS["fullstack"]: "原可观测性笔记"},
            "completed": ["agent-agent-loop"],
            "runs": [record(1)],
            "practice": [
                {
                    "lesson_id": "fullstack-http",
                    "language": "go",
                    "completed_at": "2026-10-04T00:00:00.000Z",
                }
            ],
            **values,
        }
    )


def seed(page, value=None, track="agent"):
    goto(page, "/")
    put_progress(page, baseline() if value is None else value)
    goto(page, url(track))


def no_report(page):
    expect(report_region(page)).to_have_count(0)
    expect(lab(page).get_by_label(OBSERVATION, exact=True)).to_have_count(0)
    expect(lab(page).get_by_label("诊断笔记预览", exact=True)).to_have_count(0)


def navigate(page, path):
    page.evaluate(
        """path => {
      history.pushState({}, '', path);
      dispatchEvent(new PopStateEvent('popstate'));
    }""",
        path,
    )


def only_note_changed(before, after, lesson):
    assert {key: value for key, value in before.items() if key != "notes"} == {
        key: value for key, value in after.items() if key != "notes"
    }
    assert {key: value for key, value in before["notes"].items() if key != lesson} == {
        key: value for key, value in after["notes"].items() if key != lesson
    }


def capture(page, name):
    directory = os.getenv("E2E_SCREENSHOT_DIR")
    if directory:
        page.screenshot(path=str(Path(directory) / (name + ".png")), full_page=True)


def run(page, scenario=None, *, keyboard=False):
    if scenario is not None:
        lab(page).get_by_label("诊断场景", exact=True).select_option(scenario)
    button = lab(page).get_by_role("button", name=RUN, exact=True)
    with page.expect_response(
        lambda response: (
            urlparse(response.url).path == ENDPOINT and response.request.method == "POST"
        )
    ) as response:
        if keyboard:
            button.focus()
            page.keyboard.press("Enter")
        else:
            button.click()
    actual = response.value
    assert actual.status == 200
    assert actual.headers.get("cache-control") == "no-store"
    report = actual.json()
    assert_report(page, report)
    return report


def preview(page, report, *, wrong=False, observation=REFLECTION):
    judge(page, report, wrong=wrong)
    lab(page).get_by_label(OBSERVATION, exact=True).fill(observation)
    lab(page).get_by_role("button", name=PREVIEW, exact=True).click()
    pre = lab(page).get_by_label("诊断笔记预览", exact=True)
    expect(pre).to_be_visible()
    assert observation.strip() in pre.inner_text()
    assert page.evaluate("window.diagnosticsSentinel") is None
    return pre.inner_text()


def hold_json(page):
    page.evaluate("""() => {
      const original = window.fetch.bind(window);
      let once = true;
      window.pendingDiagnostics = undefined;
      window.fetch = (url, options) => {
        if (once && String(url).endsWith('/playground/diagnostics') && options?.method === 'POST') {
          once = false;
          // Deliberately ignore AbortSignal; later release a real Response body.
          return new Promise((resolve, reject) => {
            window.pendingDiagnostics = {resolve, reject, signal: options.signal};
          });
        }
        return original(url, options);
      };
    }""")
    lab(page).get_by_role("button", name=RUN, exact=True).click()
    page.wait_for_function("window.pendingDiagnostics !== undefined")
    expect(lab(page).get_by_role("button", name="正在运行…", exact=True)).to_be_disabled()


def release_json(page, report):
    page.evaluate(
        """async report => {
      window.pendingDiagnostics.resolve(new Response(JSON.stringify(report), {
        headers: {'Content-Type': 'application/json'}
      }));
      await new Promise(resolve => setTimeout(resolve, 0));
    }""",
        report,
    )


# Assigned from the frozen contract, before reading the implementation.
EXPECTED = {
    "success": ("success", "summary", 2, ["passed", "passed", "passed"]),
    "no_evidence": ("no_evidence", "search", 1, ["passed", "skipped", "skipped"]),
    "invalid_arguments": ("rejected", "search", 1, ["rejected", "skipped", "skipped"]),
    "timeout": ("timed_out", "read", 1, ["passed", "timed_out", "skipped"]),
}


def assert_report(page, report):
    outcome, end, count, phases = EXPECTED[report["scenario"]]
    assert report["contract_version"] == "run-diagnostics-v1"
    assert str(UUID(report["run_id"])) == report["run_id"]
    assert UUID(report["run_id"]).version == 4
    assert report["model_calls"] == 0 and report["read_only"] is True
    assert (report["outcome"], report["ended_at_stage"], report["tool_dispatch_count"]) == (
        outcome,
        end,
        count,
    )
    assert report["lesson_id"] == LESSONS[report["track"]]
    assert [stage["name"] for stage in report["stages"]] == ["search", "read", "summary"]
    assert [stage["status"] for stage in report["stages"]] == phases
    assert report["cleanup_completed"] is True
    assert report["timeout_wait_cancelled"] is (report["scenario"] == "timeout")
    assert math.isfinite(report["root"]["duration_ms"]) and report["root"]["duration_ms"] >= 0
    actual_dispatches = [stage for stage in report["stages"] if stage["tool_dispatch_count"]]
    assert len(actual_dispatches) == count
    assert [stage["operation_id"] for stage in actual_dispatches] == [
        f"{report['run_id']}:{number}" for number in range(1, count + 1)
    ]
    if outcome == "success":
        read = report["evidence"]["read_lesson"]
        assert read["id"] == report["evidence"]["found_ids"][0]
        assert report["evidence"]["summary"] == read["title"] + "：" + read["summary"]
    else:
        assert report["evidence"]["read_lesson"] is None
        assert report["evidence"]["summary"] is None
    if outcome == "no_evidence":
        assert report["evidence"]["found_ids"] == []
    if outcome == "timed_out":
        assert report["stages"][1]["tool_name"] is None
        assert report["stages"][1]["error_code"] == "deadline_exceeded"
    expect(report_region(page)).to_have_attribute("data-run-id", report["run_id"])
    expect(report_region(page).get_by_label("实际阶段时间线", exact=True)).to_be_visible()


def judge(page, report, *, wrong=False):
    for label, value in (
        ("我的终态判断", report["outcome"]),
        ("我的停止阶段判断", report["ended_at_stage"]),
        ("我的工具 dispatch 次数", "0" if wrong else str(report["tool_dispatch_count"])),
    ):
        lab(page).get_by_label(label, exact=True).select_option(value)
    lab(page).get_by_role("button", name="核对我的判断", exact=True).click()
    expect(lab(page).get_by_role("region", name="诊断反馈", exact=True)).to_contain_text(
        "本次判断与实际报告不一致" if wrong else "本次判断与实际报告一致"
    )


@pytest.mark.parametrize(
    ("track", "scenarios"),
    [
        ("agent", ["success", "no_evidence"]),
        ("fullstack", ["invalid_arguments", "timeout"]),
    ],
)
def test_lesson_entries_run_four_real_scenarios_judge_and_start_explicit_repair(
    page, track, scenarios
):
    seed(page, track=track)
    goto(page, "/lesson/" + LESSONS[track])
    entry = page.get_by_role("link", name=RUN, exact=True)
    expect(entry).to_have_attribute("href", url(track))
    entry.click()
    expect(page.get_by_role("button", name="诊断演练", exact=True)).to_be_visible()
    before = stored(page)
    calls = []
    page.on(
        "request",
        lambda request: (
            calls.append(request.url)
            if request.method == "POST" and urlparse(request.url).path == ENDPOINT
            else None
        ),
    )
    ids = set()
    for scenario in scenarios:
        report = run(page, scenario)
        assert report["track"] == track
        assert report["run_id"] not in ids
        ids.add(report["run_id"])
        lab(page).get_by_role("button", name=PREVIEW, exact=True).click()
        expect(lab(page)).to_contain_text("请先核对我的判断")
        expect(lab(page).get_by_label("诊断笔记预览", exact=True)).to_have_count(0)
        judge(page, report, wrong=True)
        lab(page).get_by_label("我的工具 dispatch 次数", exact=True).select_option(
            str(report["tool_dispatch_count"])
        )
        expect(lab(page).get_by_role("region", name="诊断反馈", exact=True)).to_have_count(0)
        judge(page, report)
    prior_calls = len(calls)
    lab(page).get_by_role("button", name="切换到正常场景", exact=True).click()
    expect(lab(page).get_by_label("诊断场景", exact=True)).to_have_value("success")
    no_report(page)
    assert len(calls) == prior_calls, "修复入口只改场景，必须另由用户执行"
    if track == "agent":
        page.set_viewport_size({"width": 375, "height": 812})
    repaired = run(page, keyboard=True)
    assert repaired["run_id"] not in ids
    judge(page, repaired)
    if track == "agent":
        preview(page, repaired)
        note_preview = lab(page).get_by_label("诊断笔记预览", exact=True)
        assert note_preview.evaluate(
            "node => node.clientHeight <= 384 && node.scrollHeight > node.clientHeight"
        )
        note_preview.focus()
        note_preview.press("End")
        page.wait_for_function("document.querySelector('[aria-label=诊断笔记预览]').scrollTop > 0")
        note_preview.evaluate("node => { node.scrollTop = 0; }")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        capture(page, "deep-ai-diagnostics-mobile")
        page.set_viewport_size({"width": 1280, "height": 1000})
        capture(page, "deep-ai-diagnostics-desktop")
    assert stored(page) == before, "诊断/判断/预览都不能授予完成、实践或写模型历史"
    goto(page, "/lesson/agent-mcp")
    expect(page.get_by_role("link", name=RUN, exact=True)).to_have_count(0)


def test_wrong_judgment_can_be_reflected_previewed_once_and_roundtrip_backup(page):
    seed(page, track="fullstack")
    before = stored(page)
    report = run(page, "timeout")
    entry = preview(page, report, wrong=True)
    assert report["run_id"] in entry
    assert "run-diagnostics-v1" in entry
    assert "timed_out" in entry and "read" in entry
    assert "deadline_exceeded" in entry
    assert "0" in entry and "1" in entry
    # Editing the observation invalidates the explicit preview before saving.
    observation = REFLECTION + "\n新增观察：本地等待取消不能证明供应商被取消。"
    lab(page).get_by_label(OBSERVATION, exact=True).fill(observation)
    expect(lab(page).get_by_label("诊断笔记预览", exact=True)).to_have_count(0)
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
    lab(page).get_by_role("button", name=PREVIEW, exact=True).click()
    entry = lab(page).get_by_label("诊断笔记预览", exact=True).inner_text()
    lab(page).get_by_role("button", name=SAVE, exact=True).evaluate(
        "button => { button.click(); button.click(); }"
    )
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    only_note_changed(before, after, LESSONS["fullstack"])
    note = after["notes"][LESSONS["fullstack"]]
    assert note == before["notes"][LESSONS["fullstack"]] + "\n\n" + entry
    assert observation in note
    backup = export_record(page)
    assert backup["version"] == 1 and backup["notes"] == after["notes"]
    page.evaluate("key => localStorage.removeItem(key)", KEY)
    goto(page, "/lesson/" + LESSONS["fullstack"])
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("")
    dialog = import_record(page, backup)
    expect(dialog.get_by_role("status")).to_contain_text("学习记录已导入")
    dialog.get_by_role("button", name="关闭弹窗", exact=True).click()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    page.reload()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    assert stored(page)["runs"] == before["runs"]
    assert stored(page)["practice"] == before["practice"]


def test_latest_capacity_preserves_observation_and_newest_notes_then_consumes_saved_run(page):
    seed(page)
    report = run(page, "no_evidence")
    entry = preview(page, report)
    before = stored(page)
    full = copy.deepcopy(before)
    full["notes"][LESSONS["agent"]] = "满" * 10000
    external(page, full)
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page)).to_contain_text("空间不足")
    expect(lab(page).get_by_label(OBSERVATION, exact=True)).to_have_value(REFLECTION)
    expect(lab(page).get_by_label("诊断笔记预览", exact=True)).to_have_text(entry)
    assert stored(page) == full
    latest = copy.deepcopy(before)
    latest["notes"][LESSONS["agent"]] = "另一标签页刚刚完成的新笔记"
    latest["notes"]["fullstack-launch"] = "独立课程新文本"
    put_progress(page, latest)
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    only_note_changed(latest, after, LESSONS["agent"])
    assert after["notes"][LESSONS["agent"]] == latest["notes"][LESSONS["agent"]] + "\n\n" + entry
    removed = copy.deepcopy(after)
    removed["notes"][LESSONS["agent"]] = "用户后来明确删除摘要"
    put_progress(page, removed)
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    assert stored(page) == removed


@pytest.mark.parametrize("stage", ["preview", "save"])
def test_silent_reset_rejects_completed_old_report_and_late_event_cannot_revive_it(page, stage):
    seed(page)
    old = run(page)
    judge(page, old)
    lab(page).get_by_label(OBSERVATION, exact=True).fill("旧上下文诊断观察")
    if stage == "save":
        lab(page).get_by_role("button", name=PREVIEW, exact=True).click()
    before = stored(page)
    fresh = progress(
        history_reset_id=EPOCH_B, language="typescript", notes={"fullstack-http": "导入后的新记录"}
    )
    external(page, fresh)
    lab(page).get_by_role(
        "button", name=PREVIEW if stage == "preview" else SAVE, exact=True
    ).click()
    no_report(page)
    expect(lab(page)).to_contain_text("未保存")
    assert stored(page) == fresh
    current = run(page)
    release_storage_event(page, before)
    expect(report_region(page)).to_have_attribute("data-run-id", current["run_id"])
    entry = preview(page, current, observation="仅保存新上下文中的显式观察")
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    only_note_changed(fresh, after, LESSONS["agent"])
    assert after["notes"][LESSONS["agent"]] == entry
    assert old["run_id"] not in json.dumps(after)
    assert "旧上下文诊断观察" not in json.dumps(after, ensure_ascii=False)


def test_ignored_abort_late_json_after_stop_scenario_or_navigation_cannot_replace_new_run(page):
    seed(page)
    track = "agent"
    expected_storage = baseline()
    for action in ("stop", "scenario", "navigation", "reset"):
        old_response = page.request.post(
            base_url() + ENDPOINT,
            data={"track": track, "lesson_id": LESSONS[track], "scenario": "success"},
        )
        assert old_response.status == 200
        old = old_response.json()
        hold_json(page)
        if action == "stop":
            lab(page).get_by_role("button", name="停止等待", exact=True).click()
        elif action == "scenario":
            lab(page).get_by_label("诊断场景", exact=True).select_option("no_evidence")
        elif action == "navigation":
            track = "fullstack"
            navigate(page, url(track))
            expect(page.get_by_label("学习方向", exact=True)).to_have_value(track)
        else:
            expected_storage = baseline(
                history_reset_id=EPOCH_B,
                notes={LESSONS["fullstack"]: "静默重置后仅保留的新笔记"},
            )
            external(page, expected_storage)
            release_json(page, old)
            expect(lab(page)).to_contain_text("未保存")
        page.wait_for_function("window.pendingDiagnostics.signal.aborted")
        no_report(page)
        current = run(page, "success")
        if action != "reset":
            release_json(page, old)
        expect(report_region(page)).to_have_attribute("data-run-id", current["run_id"])
        expect(report_region(page)).not_to_contain_text(old["run_id"])
        expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_be_enabled()
    assert stored(page) == expected_storage


def test_failed_or_invalid_rerun_clears_old_report_and_does_not_echo_diagnostics(page):
    seed(page)
    old = run(page)
    preview(page, old)
    for mode in ("failure", "invalid_report"):
        if mode == "failure":
            page.route(
                "**" + ENDPOINT,
                lambda route: route.fulfill(
                    status=500, json={"detail": "private-diagnostics-do-not-store"}
                ),
                times=1,
            )
        else:
            damaged = {**old, "run_id": "private-diagnostics-do-not-store"}
            page.route(
                "**" + ENDPOINT,
                lambda route, *, payload=damaged: route.fulfill(status=200, json=payload),
                times=1,
            )
        lab(page).get_by_role("button", name=RUN, exact=True).click()
        expect(lab(page).get_by_role("status")).to_contain_text("未取得有效运行报告")
        no_report(page)
        expect(lab(page)).not_to_contain_text("private-diagnostics-do-not-store")
        assert stored(page)["notes"] == baseline()["notes"]
        expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_be_enabled()


def test_storage_failure_keeps_diagnostic_note_in_memory_for_export(page):
    seed(page)
    report = run(page, "invalid_arguments")
    entry = preview(page, report)
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
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name="已追加到本页内存", exact=True)).to_be_disabled()
    expect(page.locator("body")).to_contain_text("浏览器存储不可用")
    backup = export_record(page)
    only_note_changed(disk, backup, LESSONS["agent"])
    assert backup["notes"][LESSONS["agent"]] == disk["notes"][LESSONS["agent"]] + "\n\n" + entry
    assert stored(page) == disk
