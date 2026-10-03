import json
import os

import pytest
from playwright.sync_api import expect, sync_playwright

pytestmark = pytest.mark.e2e


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        yield page
        context.close()
        browser.close()


def goto(page, path="/"):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    page.wait_for_load_state("networkidle")


def test_course_completion_and_notes_survive_refresh(page):
    goto(page, "/lesson/agent-agent-loop")
    complete = page.get_by_role("button", name="标记本课完成", exact=True)
    expect(complete).to_be_disabled()
    page.get_by_role("radio").nth(1).check()
    for checkbox in page.get_by_role("checkbox").all():
        checkbox.check()
    page.get_by_label("课程笔记").fill("循环必须有停止条件")
    complete.click()
    page.reload()
    expect(page.get_by_role("button", name="取消完成标记")).to_be_visible()
    expect(page.get_by_label("课程笔记")).to_have_value("循环必须有停止条件")
    page.get_by_role("link", name="我的学习库", exact=True).click()
    expect(page.get_by_role("heading", name="从聊天到 Agent 循环", exact=True)).to_be_visible()


def test_demo_workflow_history_and_cancellation(page):
    goto(page, "/playground")
    page.get_by_label("任务描述").fill("MCP 工具应该如何处理授权和幂等？")
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    expect(
        page.get_by_role("heading", name="教学演示 · 课程检索工作流", exact=True)
    ).to_be_visible()
    expect(page.get_by_text("没有调用语言模型", exact=False)).to_be_visible()
    page.get_by_role("button", name="运行历史", exact=False).click()
    expect(page.get_by_text("最近运行 · 当前浏览器", exact=True)).to_be_visible()
    page.get_by_role("button", name="运行实验", exact=True).click()
    page.get_by_role("button", name="停止运行", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("运行已停止")
    expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()


def test_language_code_checks_and_failures(page):
    goto(page, "/playground?track=fullstack&mode=code")
    editor = page.get_by_label("代码编辑器")
    page.get_by_label("代码语言").select_option("python")
    editor.fill("def broken(:\n pass")
    page.get_by_role("button", name="检查代码", exact=True).click()
    expect(page.get_by_role("heading", name="还有需要修改的地方", exact=True)).to_be_visible()
    for language, source in [
        ("python", "def answer():\n    return 42"),
        ("typescript", "export const answer = () => 42;"),
        ("go", "package main\nfunc main() {}"),
    ]:
        page.get_by_label("代码语言").select_option(language)
        editor.fill(source)
        page.get_by_role("button", name="检查代码", exact=True).click()
        expect(page.get_by_role("heading", name="基础检查通过", exact=True)).to_be_visible()
        expect(page.get_by_text("未执行代码", exact=True)).to_be_visible()


def test_bookmarks_and_search(page):
    goto(page, "/feed")
    page.get_by_role("button", name="收藏：MCP：把工具接入变成清晰的协议边界", exact=True).click()
    page.reload()
    expect(
        page.get_by_role("button", name="取消收藏：MCP：把工具接入变成清晰的协议边界", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="已收藏", exact=True).click()
    expect(
        page.get_by_role("heading", name="MCP：把工具接入变成清晰的协议边界", exact=False)
    ).to_be_visible()
    page.get_by_role("button", name="搜索课程与知识", exact=False).click()
    page.get_by_label("搜索课程", exact=True).fill("FastAPI")
    expect(page.get_by_role("dialog")).to_contain_text("路由与分层架构")
    page.get_by_role("button", name="关闭弹窗", exact=True).click()


def test_news_bookmark_survives_source_loss_and_reload(page):
    article = {
        "id": "live-test",
        "title": "离线仍可回看的官方动态",
        "summary": "资料摘要已保存",
        "source": "官方测试源",
        "url": "https://go.dev/blog/context",
        "track": "fullstack",
        "tags": ["Go"],
        "kind": "news",
        "published": "2026-10-03T00:00:00Z",
    }
    page.route(
        "**/api/feed*",
        lambda route: route.fulfill(
            json={
                "items": [article],
                "sources": [],
                "fetched_at": "2026-10-03",
                "mode": "live+curated",
            }
        ),
    )
    goto(page, "/feed")
    page.get_by_role("button", name="收藏：离线仍可回看的官方动态", exact=True).click()
    page.unroute_all()
    page.route("**/api/feed*", lambda route: route.abort())
    goto(page, "/library")
    page.get_by_role("button", name="收藏资料", exact=True).click()
    expect(page.get_by_role("heading", name=article["title"], exact=True)).to_be_visible()
    page.reload()
    page.get_by_role("button", name="收藏资料", exact=True).click()
    expect(page.get_by_role("heading", name=article["title"], exact=True)).to_be_visible()
    progress = json.loads(page.evaluate("localStorage.getItem('deep-ai-station:v1')"))
    assert progress["savedItems"][0]["url"] == article["url"]


def test_mobile_navigation_has_no_horizontal_overflow(page):
    page.set_viewport_size({"width": 375, "height": 812})
    goto(page)
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    page.get_by_role("button", name="打开导航", exact=True).click()
    page.get_by_role("link", name="AI 全栈路线", exact=True).click()
    expect(page.get_by_role("heading", name="AI 全栈工程", exact=True)).to_be_visible()
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_role("link", name="HTTP 与 API 契约", exact=False).click()
    expect(page.get_by_role("heading", name="Go 服务端参考", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_import_rejects_bad_data_and_exports_without_credentials(page):
    goto(page)
    page.get_by_role("button", name="学习偏好与数据", exact=True).click()
    page.locator('input[type="file"]').set_input_files(
        {"name": "invalid.json", "mimeType": "application/json", "buffer": b'{"version":999}'}
    )
    expect(page.get_by_role("status")).to_contain_text("学习记录格式不正确")
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    assert download.value.suggested_filename == "deep-ai-station-progress.json"
