"""Curated reading links preserve course, language, and independent learning records."""

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from backend.feed import CURATED

pytestmark = pytest.mark.e2e
STORAGE_KEY = "deep-ai-station:v1"
ITEMS = {item["id"]: item for item in CURATED}
BASE_PROGRESS = {
    "version": 1,
    "completed": ["agent-agent-loop"],
    "bookmarks": [],
    "notes": {"fullstack-routing": "保留原笔记", "agent-mcp": "协议与授权分开"},
    "language": "go",
    "runs": [],
    "practice": [
        {
            "lesson_id": "fullstack-routing",
            "language": "go",
            "completed_at": "2026-10-03T12:00:00.000Z",
        }
    ],
}


def feed_payload(items=None):
    return {
        "items": CURATED if items is None else items,
        "sources": [],
        "fetched_at": "2026-10-04T00:00:00Z",
        "mode": "curated",
    }


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 1000})
        context.add_init_script(
            "if (location.protocol === 'http:' && !localStorage.getItem('deep-ai-station:v1')) "
            "localStorage.setItem('deep-ai-station:v1', "
            + json.dumps(json.dumps(BASE_PROGRESS, ensure_ascii=False))
            + ");"
        )
        context.route("**/api/feed*", lambda route: route.fulfill(json=feed_payload()))
        requests, errors = [], []
        context.on("request", lambda request: requests.append(request.url))
        context.on("page", lambda tab: tab.on("pageerror", lambda error: errors.append(str(error))))
        try:
            yield context.new_page()
            assert not errors
            assert not any(
                url.endswith(("/playground/run", "/playground/execute")) for url in requests
            )
        finally:
            context.close()
            browser.close()


def goto(page, path):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    page.wait_for_load_state("networkidle")


def stored(page):
    return json.loads(page.evaluate("key => localStorage.getItem(key)", STORAGE_KEY))


def card(page, item_id):
    return page.get_by_role("article").filter(
        has=page.get_by_role("heading", name=ITEMS[item_id]["title"], exact=True)
    )


def course_link(container):
    return container.get_by_role("link", name="配套课程：", exact=False)


def unchanged_learning(page):
    value = stored(page)
    for key in ("version", "completed", "notes", "runs", "practice"):
        assert value[key] == BASE_PROGRESS[key]


@pytest.mark.parametrize(
    ("item_id", "lesson_id", "language", "label"),
    [
        ("agents-sdk", "agent-agent-loop", "python", "Python"),
        ("mcp-guide", "agent-mcp", "python", "Python"),
        ("langgraph", "agent-state-machine", "python", "Python"),
        ("evals", "agent-datasets", "python", "Python"),
        ("fastapi-guide", "fullstack-routing", "python", "Python"),
        ("hono-guide", "fullstack-routing", "typescript", "TypeScript"),
        ("go-guide", "fullstack-async", "go", "Go"),
        ("jotai-guide", "fullstack-jotai", "go", "Go"),
    ],
)
def test_all_curated_links_use_actual_course_language_without_completing_work(
    page, item_id, lesson_id, language, label
):
    goto(page, "/feed")
    item = card(page, item_id)
    original = item.get_by_role("link", name=ITEMS[item_id]["title"], exact=False)
    expect(original).to_have_attribute("href", ITEMS[item_id]["url"])
    expect(original).to_have_attribute("target", "_blank")
    expect(original).to_have_attribute("rel", "noreferrer")
    link = course_link(item)
    expected_path = "/lesson/" + lesson_id
    if lesson_id.startswith("fullstack") and item_id != "jotai-guide":
        expected_path += "?language=" + language
    expect(link).to_have_attribute("href", expected_path)
    expect(item).to_contain_text(label)
    if item_id == "jotai-guide":
        expect(item).to_contain_text("公共前端 · Go 服务端参考")
    unchanged_learning(page)
    assert stored(page)["language"] == "go"
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**" + expected_path)
    expect(page.get_by_role("region", name="本课实践记录")).to_contain_text("当前实践：" + label)
    expect(page.get_by_role("link", name="下载本课练习资料")).to_have_attribute(
        "href", f"/api/lessons/{lesson_id}/exercise.zip?language={language}"
    )
    assert stored(page)["language"] == ("go" if lesson_id.startswith("agent") else language)
    unchanged_learning(page)
    page.reload()
    expect(page.get_by_role("region", name="本课实践记录")).to_contain_text("当前实践：" + label)
    unchanged_learning(page)


def test_copied_language_link_download_playground_and_manual_switch(page):
    goto(page, "/feed")
    href = course_link(card(page, "fastapi-guide")).get_attribute("href")
    other = page.context.new_page()
    try:
        goto(other, href + "&source=reading")
        expect(other.get_by_role("heading", name="Python 服务端参考", exact=True)).to_be_visible()
        with other.expect_download() as download:
            other.get_by_role("link", name="下载本课练习资料", exact=True).click()
        assert download.value.suggested_filename == "fullstack-routing-python.zip"
        other.get_by_role("link", name="在实验空间编辑", exact=True).click()
        expect(other.get_by_label("代码语言", exact=True)).to_have_value("python")
        other.go_back()
        other.get_by_label("课程笔记", exact=True).fill("复制链接进入后保留的笔记")
        criteria = other.get_by_role("region", name="本课验收", exact=True)
        criteria.get_by_role("checkbox").first.check()
        other.get_by_role("group", name="本课参考语言").get_by_role(
            "button", name="Go", exact=True
        ).click()
        assert "language=go" in other.url and "source=reading" in other.url
        expect(other.get_by_role("heading", name="Go 服务端参考", exact=True)).to_be_visible()
        expect(criteria.get_by_role("checkbox").first).to_be_checked()
        expect(other.get_by_label("课程笔记", exact=True)).to_have_value("复制链接进入后保留的笔记")
        other.reload()
        expect(other.get_by_role("heading", name="Go 服务端参考", exact=True)).to_be_visible()
        assert stored(other)["language"] == "go"
    finally:
        other.close()


@pytest.mark.parametrize("query", ["language=ruby", "language=python&language=go", "language="])
def test_invalid_or_ambiguous_language_query_preserves_preference(page, query):
    goto(page, "/lesson/fullstack-routing?" + query)
    expect(page.get_by_role("heading", name="Go 服务端参考", exact=True)).to_be_visible()
    assert stored(page)["language"] == "go"
    unchanged_learning(page)


def test_agent_and_missing_lesson_never_change_fullstack_preference(page):
    goto(page, "/lesson/agent-mcp?language=python")
    expect(page.get_by_role("heading", name="本课 Python 参考", exact=True)).to_be_visible()
    assert stored(page)["language"] == "go"
    before = stored(page)
    goto(page, "/lesson/fullstack-missing?language=typescript")
    expect(page.get_by_role("heading", name="课时不存在")).to_be_visible()
    assert stored(page) == before


def test_same_lesson_history_reapplies_each_explicit_language(page):
    goto(page, "/lesson/fullstack-routing?language=python")
    page.evaluate(
        """() => {
          history.pushState({}, '', '/lesson/fullstack-routing?language=typescript');
          dispatchEvent(new PopStateEvent('popstate'));
        }"""
    )
    expect(page.get_by_role("heading", name="TypeScript 服务端参考", exact=True)).to_be_visible()
    assert stored(page)["language"] == "typescript"
    page.go_back()
    expect(page.get_by_role("heading", name="Python 服务端参考", exact=True)).to_be_visible()
    assert stored(page)["language"] == "python"
    page.go_forward()
    expect(page.get_by_role("heading", name="TypeScript 服务端参考", exact=True)).to_be_visible()
    unchanged_learning(page)


def test_saved_course_link_survives_source_failure_and_backup_with_practice_isolation(page):
    goto(page, "/feed")
    for item_id in ("fastapi-guide", "hono-guide"):
        card(page, item_id).get_by_role(
            "button", name="收藏：" + ITEMS[item_id]["title"], exact=True
        ).click()
    course_link(card(page, "fastapi-guide")).click()
    practice = page.get_by_role("region", name="本课实践记录", exact=True)
    practice.get_by_role("checkbox").check()
    practice.get_by_role("button", name="标记 Python 实践完成", exact=True).click()
    page.get_by_role("link", name="探索信息流", exact=True).click()
    expect(card(page, "fastapi-guide")).to_contain_text("实践已记录")
    expect(card(page, "hono-guide")).to_contain_text("实践未记录")
    assert stored(page)["completed"] == BASE_PROGRESS["completed"]
    assert stored(page)["notes"] == BASE_PROGRESS["notes"]
    page.context.route(
        "**/api/feed*", lambda route: route.fulfill(status=503, json={"detail": "来源暂时不可用"})
    )
    goto(page, "/library")
    page.get_by_role("button", name="收藏资料", exact=True).click()
    expect(course_link(card(page, "fastapi-guide"))).to_be_visible()
    expect(card(page, "fastapi-guide")).to_contain_text("实践已记录")
    expect(card(page, "hono-guide")).to_contain_text("实践未记录")
    assert page.locator("a a").count() == 0
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    backup = json.loads(Path(download.value.path()).read_text())
    assert backup["version"] == 1 and len(backup["savedItems"]) == 2
    assert all(set(item) == set(CURATED[0]) for item in backup["savedItems"])
    page.evaluate("key => localStorage.removeItem(key)", STORAGE_KEY)
    goto(page, "/library")
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    page.locator('input[type="file"]').set_input_files(
        {
            "name": "saved-courses.json",
            "mimeType": "application/json",
            "buffer": json.dumps(backup, ensure_ascii=False).encode(),
        }
    )
    expect(page.get_by_role("status")).to_contain_text("学习记录已导入")
    page.keyboard.press("Escape")
    page.get_by_role("button", name="收藏资料", exact=True).click()
    expect(card(page, "fastapi-guide")).to_contain_text("实践已记录")
    course_link(card(page, "fastapi-guide")).click()
    expect(page.get_by_role("region", name="本课实践记录")).to_contain_text("当前实践：Python")
    expect(page.get_by_role("button", name="撤销 Python 实践记录")).to_be_visible()
    goto(page, "/feed")
    page.get_by_role("button", name="已收藏", exact=True).click()
    expect(course_link(card(page, "fastapi-guide"))).to_be_visible()
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(course_link(card(page, "fastapi-guide"))).to_be_visible()


def test_rss_item_with_known_official_url_does_not_infer_a_course(page):
    news = {**ITEMS["go-guide"], "id": "live-unmapped", "kind": "news"}
    page.context.route("**/api/feed*", lambda route: route.fulfill(json=feed_payload([news])))
    goto(page, "/feed")
    expect(page.get_by_role("heading", name=news["title"], exact=True)).to_be_visible()
    expect(course_link(page)).to_have_count(0)
    unchanged_learning(page)
