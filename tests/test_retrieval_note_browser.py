"""Course notes from real free retrieval reports, plus controlled late responses."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e
STORAGE_KEY = "deep-ai-station:v1"
SAVE = "保存评测摘要到本课笔记"
SAVED = "已保存到本课笔记"
REFLECTION = "观察与下一步"


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        try:
            yield context.new_page()
        finally:
            context.close()
            browser.close()


def goto(page, path):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    page.wait_for_load_state("networkidle")


def lab(page):
    return page.get_by_role("region", name="免费检索评测", exact=True)


def stored(page):
    return json.loads(page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY) or "{}")


def run(page):
    with page.expect_response("**/api/playground/retrieval-evaluation") as response:
        lab(page).get_by_role("button", name="运行免费检索评测", exact=True).click()
    assert response.value.status == 200
    expect(lab(page).get_by_role("status")).to_contain_text("已完成 12 道")
    return response.value.json()


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


@pytest.mark.parametrize(
    ("track", "lesson_id"), [("agent", "agent-reranking"), ("fullstack", "fullstack-ai-rag")]
)
def test_real_evaluation_note_preserves_learning_and_roundtrips_backup(page, track, lesson_id):
    requests = []
    errors = []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    goto(page, "/")
    progress = {
        "version": 1,
        "completed": ["agent-agent-loop"],
        "bookmarks": [],
        "notes": {lesson_id: "原有笔记不能覆盖", "fullstack-http": "其他课程记录"},
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
    page.evaluate("p => localStorage.setItem('deep-ai-station:v1', JSON.stringify(p))", progress)
    goto(page, "/lesson/" + lesson_id)
    before = stored(page)
    page.get_by_role("link", name="比较检索配置与指标", exact=True).click()
    lab(page).get_by_label("A 基线 top_k", exact=True).select_option("1")
    result = run(page)
    assert result["track"] == track and result["model_calls"] == 0
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
    reflection = "观察：提高召回同时增加误召回。\n下一步：检查负例，并扩大独立标注集。"
    lab(page).get_by_label(REFLECTION, exact=True).fill(reflection)
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    after = stored(page)
    notes_equal_except(before, after, lesson_id)
    note = after["notes"][lesson_id]
    assert note.startswith("原有笔记不能覆盖\n\n")
    assert reflection in note
    for value in (
        result["run_id"],
        result["dataset_version"],
        result["corpus_revision"],
        lesson_id,
    ):
        assert value in note
    for side in ("baseline", "candidate"):
        assert f"top_k={result['configurations'][side]['top_k']}" in note
        for metric in ("precision_at_k", "recall_at_k", "mrr", "no_result_accuracy"):
            value = result["metrics"][side][metric]
            text = str(int(value)) if float(value).is_integer() else str(value)
            assert text in note
    assert len(note) <= 10000
    assert note.count(result["run_id"]) == 1
    page.set_viewport_size({"width": 375, "height": 812})
    expect(lab(page).get_by_role("link", name="查看本课笔记", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    lab(page).get_by_role("link", name="查看本课笔记", exact=True).click()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    page.reload()
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    page.set_viewport_size({"width": 1280, "height": 1000})
    page.get_by_role("link", name="我的学习库", exact=True).click()
    page.get_by_role("button", name="我的笔记", exact=True).click()
    expect(page.locator(".notes-grid article").filter(has_text=lesson_id)).to_contain_text(
        reflection
    )
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
            "name": "evaluation-notes.json",
            "mimeType": "application/json",
            "buffer": json.dumps(backup, ensure_ascii=False).encode(),
        }
    )
    expect(page.get_by_role("status")).to_contain_text("学习记录已导入")
    page.keyboard.press("Escape")
    expect(page.get_by_label("课程笔记", exact=True)).to_have_value(note)
    assert stored(page)["completed"] == before["completed"]
    assert stored(page)["practice"] == before["practice"]
    assert not any(url.endswith(("/playground/run", "/playground/execute")) for url in requests)
    assert not errors


def test_changing_configuration_and_failed_rerun_never_save_old_report(page):
    goto(page, "/playground?track=agent&lesson=agent-reranking&mode=evaluation")
    first = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("只属于首次评测的判断")
    lab(page).get_by_label("B 对照 top_k", exact=True).select_option("1")
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_count(0)
    second = run(page)
    assert first["run_id"] != second["run_id"]
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_value("")
    lab(page).get_by_label(REFLECTION, exact=True).fill("也不能自动保存第二次判断")
    page.route(
        "**/api/playground/retrieval-evaluation",
        lambda route: route.fulfill(status=503, json={"detail": "评测暂时不可用"}),
        times=1,
    )
    lab(page).get_by_role("button", name="运行免费检索评测", exact=True).click()
    expect(lab(page).get_by_role("alert")).to_contain_text("评测暂时不可用")
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
    assert not stored(page).get("notes", {}).get("agent-reranking")


def test_live_note_changes_update_capacity_and_preserve_latest_other_tab_text(page):
    goto(page, "/playground?track=agent&lesson=agent-reranking&mode=evaluation")
    result = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("使用实际新笔记追加")
    other = page.context.new_page()
    try:
        goto(other, "/lesson/agent-reranking")
        other.get_by_label("课程笔记", exact=True).fill("满" * 10000)
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_disabled()
        assert stored(page)["notes"]["agent-reranking"] == "满" * 10000
        other.get_by_label("课程笔记", exact=True).fill("其他标签页刚刚修改的笔记")
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
        lab(page).get_by_role("button", name=SAVE, exact=True).click()
        expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
        note = stored(page)["notes"]["agent-reranking"]
        assert note.startswith("其他标签页刚刚修改的笔记\n\n")
        assert note.count(result["run_id"]) == 1
        expect(other.get_by_label("课程笔记", exact=True)).to_have_value(note)
        # Removing the entry in another tab restores eligibility without stale success state.
        other.get_by_label("课程笔记", exact=True).fill("显式移除已保存摘要")
        expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_be_enabled()
        lab(page).get_by_role("button", name=SAVE, exact=True).click()
        assert stored(page)["notes"]["agent-reranking"].count(result["run_id"]) == 1
    finally:
        other.close()


@pytest.mark.parametrize("lesson", ["", "&lesson=agent-no-such-course", "&lesson=fullstack-ai-rag"])
def test_missing_or_mismatched_course_keeps_free_report_without_note_write(page, lesson):
    goto(page, "/playground?track=agent&mode=evaluation" + lesson)
    result = run(page)
    assert result["track"] == "agent"
    expect(lab(page).get_by_role("button", name="导出评测 JSON", exact=True)).to_be_visible()
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_count(0)
    assert not stored(page).get("notes")


def test_completed_report_and_reflection_clear_on_same_track_course_change(page):
    goto(page, "/playground?track=agent&lesson=agent-reranking&mode=evaluation")
    first = run(page)
    lab(page).get_by_label(REFLECTION, exact=True).fill("旧课的解释")
    change_lesson_in_place(page, "agent-datasets")
    expect(lab(page).get_by_role("status")).to_contain_text("尚未运行")
    expect(lab(page).get_by_role("button", name=SAVE, exact=True)).to_have_count(0)
    second = run(page)
    expect(lab(page).get_by_label(REFLECTION, exact=True)).to_have_value("")
    lab(page).get_by_label(REFLECTION, exact=True).fill("新课的新判断")
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    notes = stored(page)["notes"]
    assert "agent-reranking" not in notes
    assert second["run_id"] in notes["agent-datasets"]
    assert first["run_id"] not in notes["agent-datasets"]


def test_aborted_old_course_response_cannot_replace_new_report_or_note(page):
    goto(page, "/playground?track=agent&lesson=agent-reranking&mode=evaluation")
    response = page.request.post(
        os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + "/api/playground/retrieval-evaluation",
        data={
            "track": "agent",
            "baseline": {"strategy": "title", "top_k": 3},
            "candidate": {"strategy": "weighted", "top_k": 3},
        },
    )
    assert response.status == 200
    old = response.json()
    page.evaluate(
        """() => {
          const original = window.fetch.bind(window);
          let intercept = true;
          window.fetch = (url, options) => {
            if (intercept && String(url).endsWith('/playground/retrieval-evaluation')) {
              intercept = false;
              window.oldEvaluationSignal = options.signal;
              return new Promise(resolve => { window.releaseOldEvaluation = resolve; });
            }
            return original(url, options);
          };
        }"""
    )
    lab(page).get_by_role("button", name="运行免费检索评测", exact=True).click()
    page.wait_for_function("window.releaseOldEvaluation !== undefined")
    change_lesson_in_place(page, "agent-datasets")
    expect(lab(page).get_by_role("status")).to_contain_text("尚未运行")
    assert page.evaluate("window.oldEvaluationSignal.aborted")
    current = run(page)
    page.evaluate(
        """async report => {
          window.releaseOldEvaluation(new Response(JSON.stringify(report), {
            headers: {'Content-Type': 'application/json'}
          }));
          await new Promise(resolve => setTimeout(resolve, 0));
        }""",
        old,
    )
    expect(lab(page).get_by_text("运行 ID：" + current["run_id"], exact=True)).to_be_visible()
    expect(lab(page).get_by_text("运行 ID：" + old["run_id"], exact=True)).to_have_count(0)
    lab(page).get_by_label(REFLECTION, exact=True).fill("仅当前课程的结果可以保存")
    lab(page).get_by_role("button", name=SAVE, exact=True).click()
    expect(lab(page).get_by_role("button", name=SAVED, exact=True)).to_be_disabled()
    notes = stored(page)["notes"]
    assert "agent-reranking" not in notes
    assert current["run_id"] in notes["agent-datasets"]
    assert old["run_id"] not in notes["agent-datasets"]
