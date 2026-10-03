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


def test_lesson_download_uses_selected_language(page):
    goto(page, "/roadmap/fullstack")
    page.get_by_role("button", name="Go", exact=True).click()
    page.get_by_role("link", name="HTTP 与 API 契约", exact=False).click()
    with page.expect_download() as download:
        page.get_by_role("link", name="下载本课练习资料", exact=True).click()
    assert download.value.suggested_filename == "fullstack-http-go.zip"


def test_capstone_project_downloads_include_the_selected_server_and_locked_frontend(page):
    import zipfile

    goto(page, "/lesson/fullstack-integration")
    expect(page.get_by_role("region", name="毕业项目骨架")).to_be_visible()
    for label, language, entry, lock in [
        ("Python", "python", "backend/app.py", "backend/uv.lock"),
        ("TypeScript", "typescript", "backend/src/app.ts", "backend/pnpm-lock.yaml"),
        ("Go", "go", "backend/main.go", "backend/go.sum"),
    ]:
        page.get_by_role("button", name=label, exact=True).click()
        with page.expect_download() as download:
            page.get_by_role("link", name=f"下载完整项目骨架 · {label}", exact=True).click()
        assert download.value.suggested_filename == f"fullstack-{language}.zip"
        with zipfile.ZipFile(download.value.path()) as archive:
            files = archive.namelist()
            assert entry in files and lock in files
            assert "frontend/pnpm-lock.yaml" in files and "frontend/src/main.tsx" in files
            assert "frontend/src/response.ts" in files
            assert "frontend/src/response.test.mjs" in files
            assert "AGENTS.md" in files and "EVIDENCE.md" in files
            assert not any(
                "node_modules" in name or ".venv" in name or name.endswith(".env") for name in files
            )
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_agent_capstone_download_contains_its_own_evidence_and_locked_project(page):
    import zipfile

    goto(page, "/lesson/agent-research-agent")
    expect(page.get_by_role("region", name="Agent 毕业项目骨架")).to_be_visible()
    project = page.get_by_role("link", name="下载 Agent 研究助手骨架", exact=True)
    expect(project).to_have_attribute("href", "/starters/agent-research.zip")
    with page.expect_download() as download:
        project.click()
    assert download.value.suggested_filename == "agent-research.zip"
    with zipfile.ZipFile(download.value.path()) as archive:
        files = archive.namelist()
        required = {
            "frontend/pnpm-lock.yaml",
            "frontend/src/main.tsx",
            "frontend/src/response.ts",
            "frontend/src/response.test.mjs",
            "backend/app.py",
            "backend/engine.py",
            "backend/test_app.py",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "backend/documents.json",
            "backend/eval-cases.json",
            "README.md",
            "AGENTS.md",
            "EVIDENCE.md",
            ".gitignore",
        }
        assert required.issubset(files)
        source = Path(__file__).resolve().parents[1] / "starters" / "agent"
        for name in ("documents.json", "eval-cases.json"):
            assert archive.read(f"backend/{name}") == (source / name).read_bytes()
        assert archive.read("README.md") == (source / "README.md").read_bytes()
        assert "backend/contract-cases.json" not in files
        assert not any(
            part in {"node_modules", ".venv", "dist", ".git", "__pycache__"}
            or part.startswith(".env")
            for name in files
            for part in Path(name).parts
        )
    page.set_viewport_size({"width": 375, "height": 812})
    expect(project).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    for lesson in ("agent-research-workflow", "agent-research-release"):
        goto(page, f"/lesson/{lesson}")
        expect(page.get_by_role("link", name="下载 Agent 研究助手骨架", exact=True)).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_inline_lesson_language_comparison_keeps_notes_and_checkpoint(page):
    goto(page, "/lesson/fullstack-http")
    page.get_by_label("课程笔记").fill("相同 API 契约，不同框架")
    page.get_by_role("checkbox").first.check()
    page.get_by_role("radio").first.check()
    for name, source in [
        ("Python", "from fastapi"),
        ("TypeScript", "from 'hono'"),
        ("Go", "package main"),
    ]:
        page.get_by_role("button", name=name, exact=True).click()
        expect(page.locator(".code-block")).to_contain_text(source)
        expect(page.get_by_label("课程笔记")).to_have_value("相同 API 契约，不同框架")
        expect(page.get_by_role("checkbox").first).to_be_checked()
        expect(page.get_by_role("radio").first).to_be_checked()
    page.reload()
    expect(page.get_by_role("button", name="Go", exact=True)).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(page.get_by_label("课程笔记")).to_have_value("相同 API 契约，不同框架")
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


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


def test_course_mentor_notes_and_history_restore_the_correct_lesson(page):
    goto(page, "/lesson/fullstack-http")
    page.get_by_label("课程笔记").fill("已有的 API 契约笔记")
    page.get_by_role("link", name="向导师提问本课", exact=True).click()
    expect(page.get_by_label("任务描述")).to_contain_text("HTTP 与 API 契约")
    original_prompt = page.get_by_label("任务描述").input_value()
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    page.get_by_label("任务描述").fill("下一次实验的任务，不应该写进之前的实验笔记")
    page.get_by_role("button", name="写入本课笔记", exact=True).click()
    expect(page.get_by_role("button", name="已写入本课笔记", exact=True)).to_be_disabled()
    progress = json.loads(page.evaluate("localStorage.getItem('deep-ai-station:v1')"))
    assert progress["runs"][0]["lesson_id"] == "fullstack-http"
    assert progress["notes"]["fullstack-http"].startswith("已有的 API 契约笔记")
    assert original_prompt in progress["notes"]["fullstack-http"]
    assert "下一次实验的任务" not in progress["notes"]["fullstack-http"]
    assert progress["completed"] == []
    page.get_by_label("学习方向", exact=False).select_option("agent")
    page.get_by_role("button", name="运行历史", exact=False).click()
    page.locator(".run-history button").first.click()
    expect(page.get_by_label("学习方向", exact=False)).to_have_value("fullstack")
    expect(page.locator(".linked-lesson")).to_contain_text("HTTP 与 API 契约")
    expect(page.get_by_role("button", name="已写入本课笔记", exact=True)).to_be_disabled()
    page.set_viewport_size({"width": 375, "height": 812})
    page.get_by_role("button", name="运行历史", exact=False).click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.locator(".linked-lesson a").click()
    page.reload()
    expect(page.get_by_label("课程笔记")).to_contain_text("已有的 API 契约笔记")
    assert page.get_by_label("课程笔记").input_value().count("### 实验记录") == 1


def test_course_mentor_does_not_overwrite_a_full_note(page):
    goto(page, "/lesson/agent-mcp")
    page.get_by_label("课程笔记").fill("已有记录" * 2400)
    page.get_by_role("link", name="向导师提问本课", exact=True).click()
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="写入本课笔记", exact=True)).to_be_disabled()
    expect(page.get_by_role("status")).to_contain_text("本课笔记容量不足")
    progress = json.loads(page.evaluate("localStorage.getItem('deep-ai-station:v1')"))
    assert progress["notes"]["agent-mcp"] == "已有记录" * 2400


def test_demo_agent_loop_uses_the_selected_course_and_restores_trace_metrics_and_notes(page):
    goto(page, "/playground?track=agent&lesson=agent-mcp&workflow=agent")
    expect(page.get_by_label("工作流", exact=True)).to_have_value("agent")
    expect(page.get_by_text("预设课程工具调用顺序", exact=False)).to_be_visible()
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
    expect(page.locator(".trace-list")).to_contain_text("lesson_read")
    expect(page.locator(".markdown-output")).to_contain_text("MCP")
    expect(page.get_by_text("3 步预设流程 · 2 次工具请求", exact=True)).to_be_visible()
    expect(page.get_by_text("未调用模型", exact=True)).to_be_visible()
    page.reload()
    page.get_by_role("button", name="运行历史", exact=False).click()
    page.locator(".run-history button").first.click()
    expect(page.get_by_text("3 步预设流程 · 2 次工具请求", exact=True)).to_be_visible()
    expect(page.locator(".trace-list")).to_contain_text("lesson_read")
    expect(page.get_by_label("工作流", exact=True)).to_have_value("agent")
    page.get_by_role("button", name="写入本课笔记", exact=True).click()
    stored = json.loads(page.evaluate("localStorage.getItem('deep-ai-station:v1')"))
    assert stored["runs"][0]["workflow"] == "agent" and stored["runs"][0]["steps"] == 3
    assert "Agent 循环" in stored["notes"]["agent-mcp"] and stored["completed"] == []
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("ending", ["done", "error"])
def test_agent_native_trace_updates_and_partial_usage_are_honest_in_history(page, ending):
    page.route(
        "**/api/capabilities",
        lambda route: route.fulfill(
            json={
                "providers": [
                    {"id": "openai", "name": "OpenAI", "enabled": True, "model": "test-model"}
                ],
                "sandbox": {"languages": []},
            }
        ),
    )

    def run(route):
        assert json.loads(route.request.post_data)["workflow"] == "agent"
        assert route.request.headers["x-playground-token"] == "test-access"
        events = [("start", {"run_id": "mock-agent-run"})]
        for step in range(1, 4):
            data = {
                "id": f"run:model:{step}",
                "title": f"模型请求 {step} / 3",
                "detail": "选择只读课程工具",
                "status": "running",
            }
            events.append(("trace", data))
            failed = ending == "error" and step == 3
            events.append(
                (
                    "trace",
                    {
                        **data,
                        "detail": "供应商响应未完成" if failed else "供应商响应已完成",
                        "status": "error" if failed else "success",
                    },
                )
            )
            if step < 3:
                events.append(
                    (
                        "trace",
                        {
                            "id": f"run:tool:{step}",
                            "title": "knowledge_search" if step == 1 else "lesson_read",
                            "detail": "实际课程资料",
                            "status": "success",
                        },
                    )
                )
        events.append(("delta", {"text": "依据课程验收项回答"}))
        events.append(
            ("error", {"message": "模型响应不可用，请稍后重试"})
            if ending == "error"
            else (
                "done",
                {
                    "duration_ms": 10,
                    "usage": {"total_tokens": 12},
                    "usage_complete": False,
                    "steps": 3,
                    "tool_count": 2,
                },
            )
        )
        route.fulfill(
            content_type="text/event-stream",
            body="".join(
                f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                for event, data in events
            ),
        )

    page.route("**/api/playground/run", run)
    goto(page, "/playground?workflow=agent")
    page.get_by_label("模型服务").select_option("openai")
    page.get_by_text("高级配置", exact=True).click()
    page.get_by_label("实验访问码").fill("test-access")
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.locator(".markdown-output")).to_contain_text("依据课程验收项回答")
    for step in range(1, 4):
        assert (
            page.locator(".trace-list").get_by_text(f"模型请求 {step} / 3", exact=True).count() == 1
        )
    stored = json.loads(
        page.evaluate("localStorage.getItem('deep-ai-station:v1')") or '{"runs":[]}'
    )
    assert len(stored["runs"]) == (1 if ending == "done" else 0)
    assert "test-access" not in json.dumps(stored)
    if ending == "done":
        expect(page.get_by_text("部分模型轮次未返回用量", exact=True)).to_be_visible()
        expect(page.get_by_text("tokens · 总量 12", exact=True)).to_be_visible()
        expect(page.get_by_text("3 轮模型请求 · 2 次工具请求", exact=True)).to_be_visible()
        assert stored["runs"][0]["usage"] == {"total_tokens": 12}
        assert len(stored["runs"][0]["trace"]) == 5
        page.get_by_label("工作流", exact=True).select_option("retrieval")
        page.reload()
        page.get_by_role("button", name="运行历史", exact=False).click()
        page.locator(".run-history button").first.click()
        expect(page.get_by_label("工作流", exact=True)).to_have_value("agent")
        expect(page.get_by_text("部分模型轮次未返回用量", exact=True)).to_be_visible()
        expect(page.get_by_text("tokens · 总量 12", exact=True)).to_be_visible()
        expect(page.get_by_text("3 轮模型请求 · 2 次工具请求", exact=True)).to_be_visible()
        page.get_by_label("模型服务").select_option("openai")
        page.get_by_text("高级配置", exact=True).click()
        assert page.get_by_label("实验访问码").input_value() == ""
    else:
        expect(page.get_by_role("alert")).to_contain_text("模型响应不可用")
        expect(page.locator(".trace-list")).to_contain_text("供应商响应未完成")
        expect(page.get_by_text("运行未完成", exact=True)).to_be_visible()


@pytest.mark.parametrize("ending", ["done", "error", "truncated"])
def test_real_stream_partial_failures_and_truncation_do_not_enter_history(page, ending):
    image_requests = []
    page.route(
        "https://tracker.invalid/**",
        lambda route: (image_requests.append(route.request.url), route.abort()),
    )
    page.route(
        "**/api/capabilities",
        lambda route: route.fulfill(
            json={
                "providers": [
                    {"id": "openai", "name": "OpenAI", "enabled": True, "model": "test-model"}
                ],
                "sandbox": {"languages": []},
            }
        ),
    )

    def run(route):
        assert route.request.headers["x-playground-token"] == "test-access"
        events = [
            ("start", {"run_id": "mock-stream-run"}),
            (
                "delta",
                {
                    "text": "流式测试文本\n\n1. 成功输入\n2. 失败输入\n\n- 验收条件\n\n"
                    "![外部图片说明](https://tracker.invalid/image.png)\n\n"
                    "[危险链接](javascript:alert(1))\n\n"
                    "[凭据链接](https://private-value@example.com/page)\n\n"
                    "[官方资料](https://docs.python.org/3/)\n\n<script>alert(1)</script>"
                },
            ),
            ("error", {"message": "模型响应不可用，请稍后重试"})
            if ending == "error"
            else ("done", {"duration_ms": 10, "usage": None, "truncated": ending == "truncated"}),
        ]
        route.fulfill(
            content_type="text/event-stream",
            body="".join(
                f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                for event, data in events
            ),
        )

    page.route("**/api/playground/run", run)
    goto(page, "/playground")
    page.get_by_label("模型服务").select_option("openai")
    page.get_by_text("高级配置", exact=True).click()
    page.get_by_label("实验访问码").fill("test-access")
    page.get_by_role("button", name="运行实验", exact=True).click()
    expect(page.locator(".markdown-output")).to_contain_text("流式测试文本")
    expect(page.locator(".markdown-output")).to_contain_text("外部图片说明")
    assert page.locator(".markdown-output img, .markdown-output script").count() == 0
    assert image_requests == []
    assert page.get_by_role("link", name="危险链接", exact=True).count() == 0
    assert page.get_by_role("link", name="凭据链接", exact=True).count() == 0
    expect(page.get_by_role("link", name="官方资料", exact=True)).to_have_attribute(
        "href", "https://docs.python.org/3/"
    )
    assert (
        page.locator(".markdown-output ol").evaluate(
            "element => getComputedStyle(element).listStyleType"
        )
        == "decimal"
    )
    assert (
        page.locator(".markdown-output ul").evaluate(
            "element => getComputedStyle(element).listStyleType"
        )
        == "disc"
    )
    expect(page.get_by_role("button", name="运行实验", exact=True)).to_be_enabled()
    stored = json.loads(
        page.evaluate("localStorage.getItem('deep-ai-station:v1')") or '{"runs":[]}'
    )
    assert len(stored["runs"]) == (1 if ending == "done" else 0)
    assert "test-access" not in json.dumps(stored)
    if ending == "done":
        expect(page.get_by_text("实验已完成", exact=True)).to_be_visible()
        expect(page.get_by_text("供应商未返回用量", exact=True)).to_be_visible()
        page.reload()
        page.get_by_role("button", name="运行历史", exact=False).click()
        page.locator(".run-history button").first.click()
        expect(page.get_by_text("供应商未返回用量", exact=True)).to_be_visible()
    else:
        expect(page.get_by_role("alert")).to_be_visible()
        expect(page.get_by_text("运行未完成", exact=True)).to_be_visible()


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


def test_sandbox_ui_requires_access_code_and_renders_plain_output(page):
    page.route(
        "**/api/capabilities",
        lambda route: route.fulfill(
            json={
                "providers": [{"id": "demo", "name": "教学演示", "enabled": True, "model": None}],
                "code_execution": "static-check",
                "live_requires_access_token": True,
                "sandbox": {
                    "enabled": True,
                    "languages": ["python"],
                    "timeout_seconds": 12,
                    "network": "denied",
                },
            }
        ),
    )

    def execute(route):
        assert route.request.headers["x-playground-token"] == "test-session-code"
        assert json.loads(route.request.post_data)["code"] == "print(42)"
        route.fulfill(
            json={
                "run_id": "test-run-id",
                "passed": True,
                "status": "completed",
                "stdout": "42\n<script>not HTML</script>",
                "stderr": "",
                "truncated": False,
                "cleanup": "destroyed",
                "duration_ms": 100,
                "notice": "Mock 沙箱 UI 契约，未调用真实服务",
            }
        )

    page.route("**/api/playground/execute", execute)
    goto(page, "/playground?track=agent&mode=code")
    run = page.get_by_role("button", name="隔离运行", exact=True)
    expect(run).to_be_disabled()
    page.get_by_label("代码编辑器").fill("print(42)")
    page.get_by_label("沙箱访问码").fill("test-session-code")
    run.click()
    expect(page.get_by_role("heading", name="隔离运行完成", exact=True)).to_be_visible()
    expect(page.locator(".sandbox-output")).to_contain_text("<script>not HTML</script>")
    assert page.locator(".sandbox-output script").count() == 0
    assert "test-session-code" not in (
        page.evaluate("localStorage.getItem('deep-ai-station:v1')") or ""
    )


def test_code_edit_is_preserved_when_switching_modes(page):
    goto(page, "/playground?track=agent&mode=code")
    editor = page.get_by_label("代码编辑器")
    editor.fill("def custom():\n    return 17")
    page.get_by_role("button", name="Agent 工作流", exact=True).click()
    page.get_by_role("button", name="代码实验", exact=True).click()
    expect(editor).to_have_value("def custom():\n    return 17")


def test_code_session_edits_survive_language_and_route_changes_and_download(page):
    goto(page, "/lesson/fullstack-http")
    page.get_by_role("link", name="在实验空间编辑", exact=True).click()
    editor = page.get_by_label("代码编辑器")
    edits = {
        "typescript": "const message: string = '我的 TypeScript 草稿';\nconsole.log(message);",
        "python": "def message():\n    return '我的 Python 草稿'\n\nprint(message())",
        "go": 'package main\nimport "fmt"\nfunc main() { fmt.Println("我的 Go 草稿") }',
    }
    for language, code in edits.items():
        page.get_by_label("代码语言").select_option(language)
        editor.fill(code)
    for language, code in edits.items():
        page.get_by_label("代码语言").select_option(language)
        expect(editor).to_have_value(code)
    with page.expect_download() as download:
        page.get_by_role("button", name="下载当前代码", exact=True).click()
    assert download.value.suggested_filename == "fullstack-http-draft.go"
    assert Path(download.value.path()).read_text() == edits["go"]
    page.locator(".linked-lesson a").click()
    page.get_by_role("link", name="在实验空间编辑", exact=True).click()
    expect(page.get_by_label("代码语言")).to_have_value("go")
    expect(editor).to_have_value(edits["go"])
    page.get_by_role("button", name="恢复示例", exact=True).click()
    expect(editor).not_to_have_value(edits["go"])
    page.get_by_label("代码语言").select_option("python")
    expect(editor).to_have_value(edits["python"])
    page.get_by_role("button", name="检查代码", exact=True).click()
    expect(page.locator(".check-output")).to_contain_text("基础检查通过")
    editor.fill("")
    expect(page.locator(".check-output")).not_to_contain_text("基础检查通过")
    page.get_by_label("代码语言").select_option("go")
    page.get_by_label("代码语言").select_option("python")
    expect(editor).to_have_value("")
    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    progress = json.loads(page.evaluate("localStorage.getItem('deep-ai-station:v1')"))
    assert "我的 Go 草稿" not in json.dumps(progress, ensure_ascii=False)


def test_saved_language_is_ready_when_playground_first_opens(page):
    page.add_init_script(
        "localStorage.setItem('deep-ai-station:v1', JSON.stringify({version:1, completed:[], bookmarks:[], notes:{}, language:'go', runs:[]}));"
    )
    goto(page, "/playground?track=fullstack&mode=code")
    expect(page.get_by_label("代码语言")).to_have_value("go")
    expect(page.get_by_label("代码编辑器")).to_contain_text("package main")


def test_storage_quota_failure_keeps_memory_and_offers_export(page):
    page.add_init_script(
        "Storage.prototype.setItem = function () { throw new DOMException('quota', 'QuotaExceededError'); };"
    )
    goto(page, "/feed")
    page.get_by_role("button", name="收藏：MCP：把工具接入变成清晰的协议边界", exact=True).click()
    expect(page.get_by_role("alert")).to_contain_text("本次记录暂存在内存")
    expect(
        page.get_by_role("button", name="取消收藏：MCP：把工具接入变成清晰的协议边界", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="导出备份", exact=True).click()
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    assert download.value.suggested_filename == "deep-ai-station-progress.json"


def test_feed_counts_actual_sources_and_identifies_stale_cache(page):
    page.route(
        "**/api/feed?*",
        lambda route: route.fulfill(
            json={
                "items": [],
                "sources": [
                    {"id": "openai", "name": "OpenAI", "status": "live"},
                    {
                        "id": "langchain",
                        "name": "LangChain",
                        "status": "unavailable",
                        "cached": True,
                    },
                    {"id": "huggingface", "name": "Hugging Face Blog", "status": "cached"},
                ],
                "mode": "live+curated",
            }
        ),
    )
    goto(page, "/feed")
    expect(page.locator(".feed-count")).to_contain_text("2 / 3 个实时源可用")
    page.get_by_role("button", name="同步官方订阅", exact=True).click()
    expect(page.locator(".source-status")).to_contain_text("LangChain · 暂不可用 · 保留缓存")


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
    screenshot_dir = os.getenv("E2E_SCREENSHOT_DIR")
    if screenshot_dir:
        Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
        goto(page)
        page.screenshot(path=str(Path(screenshot_dir) / "home-desktop.png"))
    page.set_viewport_size({"width": 375, "height": 812})
    goto(page)
    if screenshot_dir:
        page.screenshot(path=str(Path(screenshot_dir) / "home-mobile.png"), full_page=True)
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
