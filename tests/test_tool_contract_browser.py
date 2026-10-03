"""Real tool-contract API and notes, with controlled failures and late responses only."""

import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e
STORAGE_KEY = "deep-ai-station:v1"
ENDPOINT = "/api/playground/tool-contract"
LESSONS = ("agent-structured-output", "agent-tool-contract")
RUN = "运行工具校验"
SAVE = "保存工具实验到本课笔记"
SAVED = "已保存到本课笔记"
REFLECTION = "观察与下一步"
SEARCH = '{"query":"MCP"}'
BASE_PROGRESS = {
    "version": 1,
    "completed": ["agent-agent-loop"],
    "bookmarks": [],
    "notes": {
        LESSONS[0]: "原有结构化输出笔记",
        LESSONS[1]: "原有工具契约笔记",
        "fullstack-http": "其他课程记录",
    },
    "language": "go",
    "runs": [],
    "practice": [
        {
            "lesson_id": "fullstack-http",
            "language": "go",
            "completed_at": "2026-10-03T12:00:00.000Z",
        }
    ],
}


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        requests, errors = [], []
        context.on("request", lambda request: requests.append(request.url))
        context.on("page", lambda tab: tab.on("pageerror", lambda error: errors.append(str(error))))
        try:
            yield context.new_page()
            assert not errors
            assert not any(
                urlparse(url).path.endswith(("/playground/run", "/playground/execute"))
                for url in requests
            ), "工具契约实验不得进入模型或沙箱执行入口"
        finally:
            context.close()
            browser.close()


def base_url():
    return os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173")


def goto(page, path):
    page.goto(base_url() + path)
    page.wait_for_load_state("networkidle")


def experiment_url(lesson_id=LESSONS[0], track="agent"):
    return f"/playground?track={track}&lesson={lesson_id}&mode=tool-contract"


def lab(page):
    return page.get_by_role("region", name="工具契约实验", exact=True)


def result_region(page):
    return page.get_by_role("region", name="工具校验结果", exact=True)


def stored(page):
    return json.loads(page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY) or "{}")


def seed(page, progress):
    goto(page, "/")
    page.evaluate("p => localStorage.setItem('deep-ai-station:v1', JSON.stringify(p))", progress)


def configure(page, tool_name="knowledge_search", arguments_json=SEARCH):
    lab(page).get_by_label("工具名称", exact=True).fill(tool_name)
    lab(page).get_by_label("JSON 参数", exact=True).fill(arguments_json)


def assert_report(page, report):
    assert report["model_calls"] == 0
    assert report["observation"]["read_only"] is True
    assert report["observation"]["operation_id"] == report["run_id"] + ":1"
    expect(result_region(page)).to_contain_text(
        "调用成功" if report["outcome"] == "success" else "调用被拒绝"
    )
    expect(result_region(page)).to_contain_text(report["run_id"])
    observations = []
    for text in result_region(page).locator("pre").all_text_contents():
        try:
            observations.append(json.loads(text))
        except json.JSONDecodeError:
            pass
    assert report["observation"] in observations, "结果必须展示服务端原始 observation JSON"


def run(page):
    with page.expect_response(
        lambda response: (
            urlparse(response.url).path == ENDPOINT and response.request.method == "POST"
        )
    ) as response:
        lab(page).get_by_role("button", name=RUN, exact=True).click()
    assert response.value.status == 200
    assert response.value.headers.get("cache-control") == "no-store"
    report = response.value.json()
    assert_report(page, report)
    return report


def assert_no_report(page):
    expect(result_region(page)).to_have_count(0)
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_count(0)


def notes_equal_except(before, after, lesson_id):
    assert {key: value for key, value in before.items() if key != "notes"} == {
        key: value for key, value in after.items() if key != "notes"
    }
    assert {key: value for key, value in before["notes"].items() if key != lesson_id} == {
        key: value for key, value in after["notes"].items() if key != lesson_id
    }


def change_lesson_in_place(page, lesson_id):
    page.evaluate(
        """id => {
          const url = new URL(location.href);
          url.searchParams.set('lesson', id);
          history.pushState({}, '', url);
          dispatchEvent(new PopStateEvent('popstate'));
        }""",
        lesson_id,
    )
    # pushState is synchronous, but React may not have committed the new keyed session yet.
    current_lesson = page.locator(f'.linked-lesson a[href="/lesson/{lesson_id}"]')
    expect(current_lesson).to_be_visible()
    expect(lab(page).get_by_role("region", name="工具 schema", exact=True)).to_contain_text(
        "本次笔记始终归属「" + current_lesson.inner_text() + "」"
    )


def real_report(page, lesson_id):
    response = page.request.post(
        base_url() + ENDPOINT,
        data={
            "track": "agent",
            "lesson_id": lesson_id,
            "tool_name": "knowledge_search",
            "arguments_json": SEARCH,
        },
    )
    assert response.status == 200
    return response.json()


def hold_next_response(page):
    page.evaluate(
        """() => {
          const original = window.fetch.bind(window);
          let intercept = true;
          window.heldToolResponse = undefined;
          window.fetch = (url, options) => {
            if (intercept && String(url).endsWith('/playground/tool-contract')
                && options?.method === 'POST') {
              intercept = false;
              return new Promise(resolve => {
                window.heldToolResponse = {resolve, signal: options.signal};
              });
            }
            return original(url, options);
          };
        }"""
    )
    lab(page).get_by_role("button", name=RUN, exact=True).click()
    page.wait_for_function("window.heldToolResponse !== undefined")
    expect(lab(page).get_by_role("button", name="正在校验…", exact=True)).to_be_disabled()


def release_response(page, report):
    page.evaluate(
        """async report => {
          window.heldToolResponse.resolve(new Response(JSON.stringify(report), {
            headers: {'Content-Type': 'application/json'}
          }));
          await new Promise(resolve => setTimeout(resolve, 0));
        }""",
        report,
    )


def test_lesson_entries_show_real_contract_search_and_read_without_mobile_overflow(page):
    for lesson_id in LESSONS:
        goto(page, "/lesson/" + lesson_id)
        entry = page.get_by_role("link", name="运行工具契约实验", exact=True)
        expect(entry).to_have_attribute("href", experiment_url(lesson_id))
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == ENDPOINT and response.request.method == "GET"
            )
        ) as catalog_response:
            entry.click()
        assert catalog_response.value.status == 200
        catalog = catalog_response.value.json()
        assert set(catalog["supported_lesson_ids"]) == set(LESSONS)
        assert catalog["limits"]["model_calls"] == 0
        expect(lab(page)).to_be_visible()
        expect(page.get_by_role("button", name="工具契约", exact=True)).to_be_visible()
        expect(lab(page)).to_contain_text("additionalProperties")
        displayed_schemas = [
            json.loads(text)
            for text in lab(page)
            .get_by_role("region", name="工具 schema")
            .locator("pre")
            .all_text_contents()
        ]
        assert displayed_schemas == [tool["parameters"] for tool in catalog["tools"]]
        configure(page)
        searched = run(page)
        assert searched["lesson_id"] == lesson_id
        assert searched["tool_name"] == "knowledge_search"
        assert searched["arguments_json"] == SEARCH
        assert 1 <= len(searched["observation"]["items"]) <= 3
        configure(page, "lesson_read", '{"lesson_id":"agent-mcp"}')
        read = run(page)
        assert read["observation"]["lesson"]["id"] == "agent-mcp"
        assert read["lesson_id"] == lesson_id
        assert read["run_id"] != searched["run_id"]
        page.set_viewport_size({"width": 375, "height": 812})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not stored(page).get("notes")


def test_valid_unmatched_query_is_success_with_an_explicit_empty_result(page):
    goto(page, experiment_url())
    for raw in ('{"query":"zzzzunmatched987654321zzzz"}', r'{"query":"\ufeff"}'):
        configure(page, arguments_json=raw)
        report = run(page)
        assert report["outcome"] == "success" and report["observation"]["items"] == []
        assert report["arguments_json"] == raw
        expect(result_region(page)).to_contain_text("无匹配")
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
        lab(page).get_by_label(REFLECTION, exact=True).fill(
            "合法参数也可能没有证据，需要修改检索词。"
        )
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    assert report["run_id"] in stored(page)["notes"][LESSONS[0]]
    assert raw in stored(page)["notes"][LESSONS[0]]


def test_real_api_rejects_unknown_tools_schema_errors_and_ambiguous_json(page):
    goto(page, experiment_url())
    cases = [
        ("delete_everything", "{}", "unknown_tool"),
        ("knowledge_search", '{"query":"MCP","limit":1}', "invalid_arguments"),
        ("knowledge_search", '{"query":123}', "invalid_arguments"),
        ("knowledge_search", '{"query":"   "}', "invalid_arguments"),
        ("knowledge_search", json.dumps({"query": "x" * 101}), "invalid_arguments"),
        ("knowledge_search", '{"query":"MCP","query":"RAG"}', "invalid_arguments_json"),
        ("knowledge_search", '{"query":', "invalid_arguments_json"),
        ("knowledge_search", "[]", "invalid_arguments_json"),
        ("knowledge_search", '{"query":NaN}', "invalid_arguments_json"),
        ("lesson_read", '{"lesson_id":"fullstack-http"}', "lesson_not_in_track"),
    ]
    for tool_name, raw, error in cases:
        configure(page, tool_name, raw)
        report = run(page)
        assert report["outcome"] == "rejected", (tool_name, raw)
        assert report["observation"]["error"] == error
        assert report["arguments_json"] == raw
    assert not stored(page).get("notes")


@pytest.mark.parametrize(
    ("lesson_id", "tool_name", "raw", "outcome"),
    [
        (LESSONS[0], "knowledge_search", ' { "query" : "MCP" } ', "success"),
        (LESSONS[1], "knowledge_search", '{"query":"MCP","limit":1}', "rejected"),
    ],
)
def test_success_and_rejection_notes_preserve_learning_refresh_and_backup(
    page, lesson_id, tool_name, raw, outcome
):
    seed(page, BASE_PROGRESS)
    goto(page, "/lesson/" + lesson_id)
    before = stored(page)
    page.get_by_role("link", name="运行工具契约实验", exact=True).click()
    configure(page, tool_name, raw)
    report = run(page)
    assert report["outcome"] == outcome
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
    reflection = "观察：工具返回证据，不代替我的判断。\n```\n下一步：补充失败样例。"
    lab(page).get_by_label(REFLECTION, exact=True).fill(reflection)
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    notes_equal_except(before, after, lesson_id)
    note = after["notes"][lesson_id]
    assert note.startswith(before["notes"][lesson_id] + "\n\n")
    for value in (report["run_id"], lesson_id, tool_name, raw, reflection):
        assert value in note
    if outcome == "rejected":
        assert report["observation"]["error"] in note
    assert len(note) <= 10000
    lab(page).get_by_role("link", name="查看本课笔记", exact=True).click()
    page.reload()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    backup = json.loads(Path(download.value.path()).read_text())
    assert backup["version"] == 1 and backup["notes"][lesson_id] == note
    page.evaluate("key => localStorage.removeItem(key)", STORAGE_KEY)
    goto(page, "/lesson/" + lesson_id)
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value("")
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    page.locator('input[type="file"]').set_input_files(
        {
            "name": "tool-contract-notes.json",
            "mimeType": "application/json",
            "buffer": json.dumps(backup, ensure_ascii=False).encode(),
        }
    )
    expect(page.get_by_role("status")).to_contain_text("学习记录已导入")
    page.keyboard.press("Escape")
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    assert stored(page)["completed"] == before["completed"]
    assert stored(page)["practice"] == before["practice"]


def test_missing_wrong_or_unsupported_course_never_offers_execution(page):
    posts = []
    page.on(
        "request",
        lambda request: (
            posts.append(request.url)
            if request.method == "POST" and urlparse(request.url).path == ENDPOINT
            else None
        ),
    )
    paths = [
        "/playground?track=agent&mode=tool-contract",
        experiment_url("agent-no-such-course"),
        experiment_url("agent-mcp"),
        experiment_url("fullstack-http"),
        experiment_url(LESSONS[0], "fullstack"),
    ]
    for path in paths:
        goto(page, path)
        expect(lab(page)).to_contain_text("请从支持的课程开始工具实验")
        expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_have_count(0)
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
        links = lab(page).get_by_role("link").evaluate_all("links => links.map(a => a.href)")
        assert all(any(lesson in href for href in links) for lesson in LESSONS)
    assert posts == []
    assert not stored(page).get("notes")
    goto(page, "/playground?track=fullstack&lesson=fullstack-http&mode=code")
    expect(page.get_by_label("学习方向", exact=True)).to_have_value("fullstack")
    expect(page.get_by_label("代码编辑器", exact=True)).to_be_visible()
    for lesson_id in LESSONS:
        page.evaluate(
            """path => {
              history.pushState({}, '', path);
              dispatchEvent(new PopStateEvent('popstate'));
            }""",
            experiment_url(lesson_id),
        )
        expect(page.get_by_label("学习方向", exact=True)).to_have_value("agent")
        expect(page.get_by_role("button", name="工具契约", exact=True)).to_have_class("selected")
        expect(page.get_by_label("代码编辑器", exact=True)).to_have_count(0)
        configure(page)
        report = run(page)
        assert report["track"] == "agent" and report["lesson_id"] == lesson_id
    assert not stored(page).get("notes")


def test_editing_each_input_and_failed_rerun_remove_previous_saveable_report(page):
    goto(page, experiment_url())
    configure(page)
    first = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("首次判断不能跟随新输入")
    lab(page).get_by_label("工具名称", exact=True).fill("lesson_read")
    assert_no_report(page)
    lab(page).get_by_label("JSON 参数", exact=True).fill('{"lesson_id":"agent-mcp"}')
    second = run(page)
    assert second["run_id"] != first["run_id"]
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_value("")
    lab(page).get_by_label("JSON 参数", exact=True).fill('{"lesson_id":"agent-tool-contract"}')
    assert_no_report(page)
    run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("失败重跑也不能保存旧报告")
    page.route(
        "**" + ENDPOINT,
        lambda route: route.fulfill(status=503, json={"detail": "工具暂时不可用"}),
        times=1,
    )
    lab(page).get_by_role("button", name=RUN, exact=True).click()
    expect(lab(page).get_by_role("alert")).to_contain_text("工具校验未完成")
    assert_no_report(page)
    assert not stored(page).get("notes")


def test_stopped_or_edited_pending_response_cannot_replace_a_new_real_report(page):
    goto(page, experiment_url())
    for action in ("stop", "edit"):
        configure(page)
        old = real_report(page, LESSONS[0])
        hold_next_response(page)
        if action == "stop":
            lab(page).get_by_role("button", name="停止等待", exact=True).click()
        else:
            lab(page).get_by_label("JSON 参数", exact=True).fill('{"query":"工具契约"}')
        assert_no_report(page)
        assert page.evaluate("window.heldToolResponse.signal.aborted")
        current = run(page)
        release_response(page, old)
        assert_report(page, current)
        expect(result_region(page)).not_to_contain_text(old["run_id"])
    assert not stored(page).get("notes")


def test_completed_report_and_reflection_clear_on_same_page_course_navigation(page):
    goto(page, experiment_url())
    configure(page)
    old = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("旧课的个人判断")
    change_lesson_in_place(page, LESSONS[1])
    assert_no_report(page)
    configure(page)
    current = run(page)
    assert current["lesson_id"] == LESSONS[1]
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_value("")
    lab(page).get_by_label(REFLECTION, exact=True).fill("新课程的新判断")
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    notes = stored(page)["notes"]
    assert LESSONS[0] not in notes
    assert current["run_id"] in notes[LESSONS[1]]
    assert old["run_id"] not in notes[LESSONS[1]]
    assert "旧课的个人判断" not in notes[LESSONS[1]]


def test_late_response_ignoring_abort_cannot_cross_courses_or_be_saved(page):
    goto(page, experiment_url())
    configure(page)
    old = real_report(page, LESSONS[0])
    hold_next_response(page)
    change_lesson_in_place(page, LESSONS[1])
    assert_no_report(page)
    page.wait_for_function("window.heldToolResponse.signal.aborted", timeout=5000)
    assert page.evaluate("window.heldToolResponse.signal.aborted")
    configure(page)
    current = run(page)
    release_response(page, old)
    assert_report(page, current)
    expect(result_region(page)).not_to_contain_text(old["run_id"])
    lab(page).get_by_label(REFLECTION, exact=True).fill("只保存当前课的真实结果")
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    notes = stored(page)["notes"]
    assert LESSONS[0] not in notes
    assert current["run_id"] in notes[LESSONS[1]]
    assert old["run_id"] not in notes[LESSONS[1]]


def test_capacity_and_saved_state_recheck_latest_other_tab_note(page):
    goto(page, experiment_url())
    configure(page)
    report = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("使用另一标签页最新笔记追加")
    other = page.context.new_page()
    try:
        goto(other, "/lesson/" + LESSONS[0])
        other.get_by_label("课程笔记", exact=True).fill("满" * 10000)
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
        assert stored(page)["notes"][LESSONS[0]] == "满" * 10000
        other.get_by_label("课程笔记", exact=True).fill("另一标签页刚刚修改的原笔记")
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
        lab(page).get_by_role("button", name=SAVE, exact=True).click()
        expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
        note = stored(page)["notes"][LESSONS[0]]
        assert note.startswith("另一标签页刚刚修改的原笔记\n\n")
        assert report["run_id"] in note
        expect(other.get_by_label("课程笔记", exact=True)).to_have_value(note)
        other.get_by_label("课程笔记", exact=True).fill("显式移除已保存的实验")
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
        lab(page).get_by_role("button", name=SAVE, exact=True).click()
        expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
        next_note = stored(page)["notes"][LESSONS[0]]
        assert next_note.startswith("显式移除已保存的实验\n\n")
        assert next_note.count(report["run_id"]) == note.count(report["run_id"])
    finally:
        other.close()


def test_note_key_capacity_is_rechecked_before_adding_a_new_course_entry(page):
    progress = {**BASE_PROGRESS, "notes": {f"agent-note-{i}": "已保留" for i in range(1000)}}
    seed(page, progress)
    goto(page, experiment_url())
    configure(page)
    report = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("笔记数量恢复后才允许追加")
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
    assert stored(page)["notes"] == progress["notes"]
    other = page.context.new_page()
    try:
        goto(other, "/library")
        other.evaluate(
            """key => {
              const latest = JSON.parse(localStorage.getItem(key));
              delete latest.notes['agent-note-0'];
              localStorage.setItem(key, JSON.stringify(latest));
            }""",
            STORAGE_KEY,
        )
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
        lab(page).get_by_role("button", name=SAVE, exact=True).click()
        expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
        after = stored(page)
        assert len(after["notes"]) == 1000
        assert report["run_id"] in after["notes"][LESSONS[0]]
        assert "agent-note-0" not in after["notes"]
        assert all(after["notes"][f"agent-note-{i}"] == "已保留" for i in range(1, 1000))
        assert after["completed"] == progress["completed"]
        assert after["practice"] == progress["practice"]
    finally:
        other.close()


def test_catalog_failure_or_invalid_schema_disables_execution_until_real_retry(page):
    source = page.request.get(base_url() + ENDPOINT)
    assert source.status == 200
    malformed = source.json()
    malformed["tools"][0]["parameters"]["additionalProperties"] = True

    def failure_handler(kind):
        def reject_catalog(route):
            if route.request.method != "GET":
                route.continue_()
            elif kind == "unavailable":
                route.fulfill(status=503, json={"detail": "catalog temporarily unavailable"})
            else:
                route.fulfill(json=malformed)

        return reject_catalog

    for failure in ("unavailable", "invalid-schema"):
        reject_catalog = failure_handler(failure)
        page.route("**" + ENDPOINT, reject_catalog)
        try:
            goto(page, experiment_url())
            expect(lab(page).get_by_role("alert")).to_contain_text("工具契约加载失败")
            expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_be_disabled()
            assert_no_report(page)
        finally:
            page.unroute("**" + ENDPOINT, reject_catalog)
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == ENDPOINT and response.request.method == "GET"
            )
        ) as retry:
            lab(page).get_by_role("button", name="重试加载工具契约", exact=True).click()
        assert retry.value.status == 200
        assert retry.value.json()["tools"][0]["parameters"]["additionalProperties"] is False
        expect(lab(page).get_by_role("alert")).to_have_count(0)
        expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_be_enabled()
        configure(page)
        assert run(page)["outcome"] == "success"


def test_timed_out_catalog_ignoring_abort_cannot_replace_successful_retry(page):
    # Install before the app creates timers; fast_forward fires the watchdog
    # without spending 15 real seconds or relying on a guessed sleep.
    page.clock.install()
    goto(page, "/playground?track=fullstack&lesson=fullstack-http&mode=code")
    source = page.request.get(base_url() + ENDPOINT)
    assert source.status == 200
    late_catalog = source.json()
    late_catalog["tools"][0]["description"] = "迟到目录不得覆盖已经恢复的真实契约"
    page.evaluate(
        """() => {
          const original = window.fetch.bind(window);
          window.holdCatalogRequests = true;
          window.heldCatalogResponses = [];
          window.fetch = (url, options) => {
            if (window.holdCatalogRequests && String(url).endsWith('/playground/tool-contract')
                && (!options?.method || options.method === 'GET')) {
              return new Promise(resolve => {
                window.heldCatalogResponses.push({resolve, signal: options.signal});
              });
            }
            return original(url, options);
          };
        }"""
    )
    page.evaluate(
        """path => {
          history.pushState({}, '', path);
          dispatchEvent(new PopStateEvent('popstate'));
        }""",
        experiment_url(),
    )
    # StrictMode may start and dispose an extra request. Wait for a live one,
    # then hold every response so neither mount can bypass the timeout branch.
    page.wait_for_function("window.heldCatalogResponses.some(item => !item.signal.aborted)")
    expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_be_disabled()
    page.clock.fast_forward(15_000)
    expect(lab(page).get_by_role("alert")).to_contain_text("工具契约加载超时，请重试。")
    assert page.evaluate("window.heldCatalogResponses.every(item => item.signal.aborted)")
    assert_no_report(page)
    page.evaluate("window.holdCatalogRequests = false")
    with page.expect_response(
        lambda response: (
            urlparse(response.url).path == ENDPOINT and response.request.method == "GET"
        )
    ) as retry:
        lab(page).get_by_role("button", name="重试加载工具契约", exact=True).click()
    assert retry.value.status == 200
    current_catalog = retry.value.json()
    expect(lab(page).get_by_role("button", name=RUN, exact=True)).to_be_enabled()
    page.evaluate(
        """async catalog => {
          for (const held of window.heldCatalogResponses) {
            held.resolve(new Response(JSON.stringify(catalog), {
              headers: {'Content-Type': 'application/json'}
            }));
          }
          await new Promise(resolve => setTimeout(resolve, 0));
        }""",
        late_catalog,
    )
    expect(lab(page).get_by_role("alert")).to_have_count(0)
    expect(lab(page)).not_to_contain_text("迟到目录不得覆盖已经恢复的真实契约")
    schemas = [
        json.loads(text)
        for text in lab(page)
        .get_by_role("region", name="工具 schema")
        .locator("pre")
        .all_text_contents()
    ]
    assert schemas == [tool["parameters"] for tool in current_catalog["tools"]]
    configure(page)
    assert run(page)["outcome"] == "success"


def test_history_navigation_aborts_hidden_agent_stream_and_ignores_late_frames(page):
    goto(page, "/playground?track=agent&mode=tool-contract")
    page.evaluate(
        """() => {
          history.pushState({}, '', '/playground?track=agent&lesson=agent-structured-output&mode=agent&workflow=agent');
          dispatchEvent(new PopStateEvent('popstate'));
          const original = window.fetch.bind(window);
          window.fetch = (url, options) => {
            if (String(url).endsWith('/playground/run') && options?.method === 'POST') {
              const payload = JSON.parse(options.body);
              if (payload.provider !== 'demo') throw new Error('Only the fixed demo is allowed');
              const encoder = new TextEncoder();
              const body = new ReadableStream({
                start(controller) {
                  const emit = (event, data) => controller.enqueue(encoder.encode(
                    `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
                  ));
                  window.heldAgentStream = {signal: options.signal, payload, emit,
                    close: () => controller.close()};
                  emit('start', {run_id: 'controlled-old-agent-run'});
                  emit('delta', {text: '旧课流的部分内容'});
                }
              });
              // This controlled transport deliberately ignores AbortSignal.
              return Promise.resolve(new Response(body, {
                headers: {'Content-Type': 'text/event-stream'}
              }));
            }
            return original(url, options);
          };
        }"""
    )
    expect(page.get_by_label("模型服务", exact=True)).to_have_value("demo")
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.locator(".markdown-output")).to_contain_text("旧课流的部分内容")
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_be_visible()
    assert page.evaluate("window.heldAgentStream.payload.lesson_id") == LESSONS[0]
    page.go_back()
    expect(lab(page)).to_contain_text("请从支持的课程开始工具实验")
    page.wait_for_function("window.heldAgentStream.signal.aborted", timeout=5000)
    assert page.evaluate("window.heldAgentStream.signal.aborted"), (
        "历史导航已隐藏 Agent 的停止按钮，但旧 Agent 请求未取消"
    )
    expect(page.get_by_role("button", name="Agent 工作流", exact=True)).to_be_enabled()
    expect(page.get_by_label("学习方向", exact=True)).to_be_enabled()
    lab(page).locator(f'a[href="{experiment_url(LESSONS[1])}"]').click()
    configure(page)
    current = run(page)
    page.evaluate(
        """async () => {
          window.heldAgentStream.emit('delta', {text: '迟到内容不得污染新课'});
          window.heldAgentStream.emit('done', {
            duration_ms: 1, model: null, usage: null, usage_complete: false,
            steps: 1, tool_count: 0
          });
          window.heldAgentStream.close();
          await new Promise(resolve => setTimeout(resolve, 0));
        }"""
    )
    assert_report(page, current)
    records = stored(page)["runs"]
    assert len(records) == 1
    assert records[0]["status"] == "cancelled"
    assert records[0]["reason"] == "context_changed"
    assert records[0]["lesson_id"] == LESSONS[0]
    assert records[0]["answer"] == "旧课流的部分内容"
    assert records[0]["server_run_id"] == "controlled-old-agent-run"
    assert not stored(page).get("notes")
    page.get_by_role("button", name="Agent 工作流", exact=True).click()
    expect(page.locator(".markdown-output")).to_have_count(0)
    expect(page.get_by_text("实验已完成", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()


@pytest.mark.parametrize("context_change", ["history", "stop"])
def test_late_code_check_cannot_replace_new_context_or_release_its_pending_request(
    page, context_change
):
    goto(page, "/playground?track=fullstack&lesson=fullstack-http&mode=code")
    page.get_by_label("代码语言", exact=True).select_option("python")
    page.get_by_label("代码编辑器", exact=True).fill("def old_check():\n    return 1")
    page.evaluate(
        """() => {
          const original = window.fetch.bind(window);
          window.heldCodeChecks = [];
          window.fetch = (url, options) => {
            if (String(url).endsWith('/playground/check') && options?.method === 'POST'
                && window.heldCodeChecks.length < 2) {
              return new Promise(resolve => {
                window.heldCodeChecks.push({resolve, signal: options.signal});
              });
            }
            return original(url, options);
          };
        }"""
    )
    page.get_by_role("button", name="检查代码", exact=True).click()
    page.wait_for_function("window.heldCodeChecks.length === 1")
    if context_change == "history":
        page.evaluate(
            """() => {
              history.pushState({}, '', '/playground?track=agent&lesson=agent-structured-output&mode=code');
              dispatchEvent(new PopStateEvent('popstate'));
            }"""
        )
        expect(page.get_by_label("学习方向", exact=True)).to_have_value("agent")
    else:
        page.get_by_role("button", name="停止运行", exact=True).click()
    page.wait_for_function("window.heldCodeChecks[0].signal.aborted", timeout=5000)
    assert page.evaluate("window.heldCodeChecks[0].signal.aborted"), (
        "切换课程或停止后，旧的代码检查请求必须取消"
    )
    expect(page.get_by_role("button", name="检查代码", exact=True)).to_be_enabled()
    source = "def current_check():\n    return 2"
    page.get_by_label("代码编辑器", exact=True).fill(source)
    page.get_by_role("button", name="检查代码", exact=True).click()
    page.wait_for_function("window.heldCodeChecks.length === 2")
    page.evaluate(
        """async () => {
          window.heldCodeChecks[0].resolve(new Response(JSON.stringify({
            mode: 'static-check', executed: false, passed: false,
            checks: [{title: '旧课检查结果不得回填', passed: false}],
            notice: '迟到检查不得解除新请求的运行状态'
          }), {headers: {'Content-Type': 'application/json'}}));
          await new Promise(resolve => setTimeout(resolve, 0));
        }"""
    )
    expect(page.locator(".check-output")).not_to_contain_text("旧课检查结果不得回填")
    expect(page.get_by_role("button", name="检查代码", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="停止运行", exact=True)).to_be_visible()
    assert not page.evaluate("window.heldCodeChecks[1].signal.aborted")
    current = page.request.post(
        base_url() + "/api/playground/check", data={"language": "python", "code": source}
    )
    assert current.status == 200
    report = current.json()
    assert report["executed"] is False and report["passed"] is True
    page.evaluate(
        """async report => {
          window.heldCodeChecks[1].resolve(new Response(JSON.stringify(report), {
            headers: {'Content-Type': 'application/json'}
          }));
          await new Promise(resolve => setTimeout(resolve, 0));
        }""",
        report,
    )
    expect(page.get_by_role("heading", name="基础检查通过", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="检查代码", exact=True)).to_be_enabled()
    expect(page.locator(".check-output")).not_to_contain_text("旧课检查结果不得回填")
    expect(page.get_by_label("代码编辑器", exact=True)).to_have_value(source)
    assert stored(page).get("runs", []) == []
    assert not stored(page).get("notes")
