"""Real React to API evaluation comparisons; no provider or sandbox calls."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
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


def run(page):
    with page.expect_response("**/api/playground/retrieval-evaluation") as response:
        lab(page).get_by_role("button", name="运行免费检索评测", exact=True).click()
    assert response.value.status == 200
    expect(lab(page).get_by_role("status")).to_contain_text("已完成 12 道")
    return response.value.json()


def test_course_evaluation_compares_real_results_and_downloads_report(page):
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    goto(page, "/lesson/agent-reranking")
    page.get_by_label("课程笔记", exact=True).fill("先比较实际召回，再记录业务验收")
    before = page.evaluate("localStorage.getItem('deep-ai-station:v1')")
    page.get_by_role("link", name="比较检索配置与指标", exact=True).click()
    expect(lab(page)).to_be_visible()
    lab(page).get_by_label("A 基线 top_k", exact=True).select_option("1")
    first = run(page)
    assert first["track"] == "agent" and first["model_calls"] == 0
    assert first["configurations"]["baseline"]["top_k"] == 1
    metrics = lab(page).get_by_role("region", name="A 基线评测指标", exact=True)
    expect(metrics).to_contain_text(f"{first['metrics']['baseline']['recall_at_k']:.3f}")
    item = first["cases"][0]
    lab(page).locator("summary").filter(has_text=item["query"]).click()
    expect(lab(page).get_by_role("heading", name="逐题排名与漏检分析")).to_be_visible()
    expect(lab(page).get_by_text("匹配词：", exact=False).first).to_be_visible()
    expect(
        lab(page).get_by_role("link", name=item["relevant"][0]["title"], exact=True).first
    ).to_have_attribute("href", "/lesson/" + item["relevant"][0]["id"])
    with page.expect_download() as download:
        lab(page).get_by_role("button", name="导出评测 JSON", exact=True).click()
    assert download.value.suggested_filename == f"retrieval-evaluation-agent-{first['run_id']}.json"
    assert json.loads(Path(download.value.path()).read_text()) == first
    lab(page).get_by_label("A 基线 top_k", exact=True).select_option("5")
    expect(lab(page).get_by_role("button", name="导出评测 JSON", exact=True)).to_have_count(0)
    expect(lab(page).get_by_role("status")).to_contain_text("配置已更新")
    second = run(page)
    assert second["metrics"]["baseline"] != first["metrics"]["baseline"]
    assert second["run_id"] != first["run_id"]
    assert second["corpus_revision"] == first["corpus_revision"]
    page.set_viewport_size({"width": 375, "height": 812})
    lab(page).locator("summary").filter(has_text=second["cases"][0]["query"]).click()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert page.evaluate("localStorage.getItem('deep-ai-station:v1')") == before
    assert not any(url.endswith(("/playground/run", "/playground/execute")) for url in requests)


def test_evaluation_recovers_from_error_and_resets_between_tracks(page):
    goto(page, "/playground?track=agent&mode=evaluation")
    page.route(
        "**/api/playground/retrieval-evaluation",
        lambda route: route.fulfill(status=503, json={"detail": "评测服务暂时不可用"}),
        times=1,
    )
    lab(page).get_by_role("button", name="运行免费检索评测", exact=True).click()
    expect(lab(page).get_by_role("alert")).to_contain_text("评测服务暂时不可用")
    first = run(page)
    expect(lab(page).get_by_role("alert")).to_have_count(0)
    page.get_by_label("学习方向", exact=True).select_option("fullstack")
    expect(lab(page).get_by_role("button", name="导出评测 JSON", exact=True)).to_have_count(0)
    expect(lab(page).get_by_role("status")).to_contain_text("尚未运行")
    second = run(page)
    assert second["track"] == "fullstack"
    assert first["corpus_revision"] != second["corpus_revision"]
    assert all(case["id"].startswith("fullstack-") for case in second["cases"])
    page.reload()
    expect(lab(page)).to_be_visible()
    expect(lab(page).get_by_role("status")).to_contain_text("尚未运行")
    goto(page, "/lesson/fullstack-ai-rag")
    expect(page.get_by_role("link", name="比较检索配置与指标", exact=True)).to_have_attribute(
        "href", "/playground?track=fullstack&lesson=fullstack-ai-rag&mode=evaluation"
    )


def test_changing_track_cancels_pending_evaluation_without_stale_results(page):
    goto(page, "/playground?track=agent&mode=evaluation")
    pending = []
    page.route("**/api/playground/retrieval-evaluation", lambda route: pending.append(route))
    with page.expect_request("**/api/playground/retrieval-evaluation"):
        lab(page).get_by_role("button", name="运行免费检索评测", exact=True).click()
    expect(lab(page).get_by_role("status")).to_contain_text("正在比较")
    assert len(pending) == 1
    with page.expect_event(
        "requestfailed",
        predicate=lambda request: request.url.endswith("/playground/retrieval-evaluation"),
        timeout=10000,
    ):
        page.get_by_label("学习方向", exact=True).select_option("fullstack")
    expect(lab(page).get_by_role("alert")).to_have_count(0)
    expect(lab(page).get_by_role("status")).to_contain_text("尚未运行")
    expect(lab(page).get_by_role("button", name="导出评测 JSON", exact=True)).to_have_count(0)
    page.unroute("**/api/playground/retrieval-evaluation")
    assert run(page)["track"] == "fullstack"
