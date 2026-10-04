"""Actual curated reading, lesson ZIP and explicit Agent practice/evidence flows.

The shared runner owns servers. These cases read the downloaded archive but never
execute its code; the standalone verifier exercises the independent Python CLI.
"""

import hashlib
import json
import os
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect
from test_evidence_library_browser import evidence
from test_lab_evidence_browser import card as evidence_card
from test_lab_evidence_browser import expect_fields, fill
from test_practice_browser import confirmation, mark, practice
from test_progress_write_browser import EPOCH_A, goto
from test_progress_write_browser import page as page
from test_run_history_browser import progress, put_progress, record, stored

from backend.curriculum import LESSONS
from backend.feed import CURATED

pytestmark = pytest.mark.e2e
LESSON_ID = "agent-agent-loop"
TITLE = "可运行 Agent 决策循环实验"
ZIP_NAME = "agent-loop-python.zip"
ASSETS_SHA256 = "45a01bfcdff94b3d96826ba2f9eab7a750a51ea3e362939d8c2a0e1d1c06007d"
TIME = "2026-10-04T12:00:00.000Z"
MEMBERS = {
    "agent_loop.py",
    "engine.py",
    "policies.py",
    "tools.py",
    "test_loop.py",
    "test_cli.py",
    "pyproject.toml",
    "uv.lock",
    "fixtures.json",
    "README.md",
    "CONTRACT.md",
    "EVIDENCE.md",
    "AGENTS.md",
    ".gitignore",
    "manifest.json",
    ".prettierrc.json",
}
VALUES = {
    "revision": "learner-recorded-loop-fixture",
    "command": "uv run python -m agent_loop --case found --policy evidence_first --max-steps 3",
    "success": "学习者自述：search → read → finish；这是填写内容，平台没有执行命令。",
    "failure": "学习者自述：预算 2 时已读正文，但没有 finish，仍为 step_limit。",
    "pending": "真实模型规划未验证；本条证据不表示平台已运行独立包。",
}


def baseline():
    return progress(
        history_reset_id=EPOCH_A,
        language="go",
        completed=[LESSON_ID, "fullstack-http"],
        notes={LESSON_ID: "保留第一课旧笔记", "fullstack-routing": "保留其他路线笔记"},
        bookmarks=["agents-sdk"],
        runs=[record(1)],
        practice=[
            {"lesson_id": "agent-mcp", "language": "python", "completed_at": TIME},
            {"lesson_id": "fullstack-routing", "language": "go", "completed_at": TIME},
        ],
        evidence=[evidence("agent-mcp", "python", command="保留原 MCP 证据")],
    )


def seed(page, path):
    # The reused guard blocks paid/external requests; remove only its feed mock.
    page.unroute("**/api/feed*")
    goto(page, "/")
    original = baseline()
    put_progress(page, original)
    goto(page, path)
    return original


def unchanged_except(before, after, *fields):
    ignored = {"resume", *fields}
    assert {key: value for key, value in before.items() if key not in ignored} == {
        key: value for key, value in after.items() if key not in ignored
    }


def lab(page):
    return page.get_by_role("region", name=TITLE, exact=True)


def checklist(page):
    return page.get_by_role("region", name="可运行实践清单", exact=True)


def group(page):
    return checklist(page).locator('article[data-lab-id="agent-loop"]')


def expand(page, *, keyboard=False):
    current = group(page)
    expect(current).to_have_attribute("data-language", "python")
    summary = current.get_by_label("查看关联课时：" + TITLE, exact=True)
    if keyboard:
        summary.focus()
        page.keyboard.press("Enter")
    else:
        summary.click()
    expect(current.locator("details")).to_have_attribute("open", "")
    return current.get_by_role(
        "link", name=f"进入实验：{LESSONS[LESSON_ID]['title']} · Python", exact=True
    )


def download_zip(page, *, keyboard=False):
    link = lab(page).get_by_role("link", name="下载实验 · Python", exact=True)
    expect(link).to_have_attribute("href", "/labs/" + ZIP_NAME)
    before = stored(page)
    with page.expect_download() as event:
        if keyboard:
            link.focus()
            page.keyboard.press("Enter")
        else:
            link.click()
    downloaded = event.value
    assert downloaded.failure() is None and downloaded.suggested_filename == ZIP_NAME
    with zipfile.ZipFile(Path(downloaded.path())) as archive:
        assert len(archive.namelist()) == 16 and set(archive.namelist()) == MEMBERS
        assert archive.testzip() is None
        assert json.loads(archive.read("manifest.json")) == {
            "id": "agent-loop",
            "version": "agent-loop-v1",
            "lessons": [LESSON_ID],
            "languages": ["python"],
        }
        fixtures = json.loads(archive.read("fixtures.json"))
        canonical = json.dumps(
            fixtures, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        assert hashlib.sha256(canonical).hexdigest() == ASSETS_SHA256
        assert fixtures["asset_version"] == "agent-loop-corpus-v1"
        assert [case["id"] for case in fixtures["cases"]] == ["found", "empty"]
        assert [document["id"] for document in fixtures["documents"]] == [
            "loop-guide",
            "tool-guide",
        ]
        assert all(document["kind"] == "teaching-summary" for document in fixtures["documents"])
        assert archive.read("agent_loop.py").strip()
        assert "python -m agent_loop" in archive.read("README.md").decode("utf-8")
        project = tomllib.loads(archive.read("pyproject.toml").decode("utf-8"))
        assert {part.strip() for part in project["project"]["requires-python"].split(",")} == {
            ">=3.12",
            "<3.13",
        }
        assert project["project"].get("dependencies", []) == []
        lock = tomllib.loads(archive.read("uv.lock").decode("utf-8"))
        assert lock["version"] == 1
        assert {"pytest", "ruff"} <= {package["name"] for package in lock["package"]}
    assert stored(page) == before, "Downloading fixed code must not record learner practice"


def test_actual_agents_sdk_reading_opens_first_lesson_and_real_python_package(page):
    original = seed(page, "/")
    with page.expect_response(
        lambda response: (
            urlparse(response.url).path == "/api/feed"
            and parse_qs(urlparse(response.url).query) == {"refresh": ["false"]}
        )
    ) as response:
        goto(page, "/feed")
    assert response.value.status == 200 and response.value.request.method == "GET"
    item = next(item for item in CURATED if item["id"] == "agents-sdk")
    article = page.get_by_role("article").filter(
        has=page.get_by_role("heading", name=item["title"], exact=True)
    )
    expect(article).to_have_count(1)
    source = article.get_by_role("link", name=item["title"], exact=False)
    expect(source).to_have_attribute("href", "https://openai.github.io/openai-agents-python/")
    expect(source).to_have_attribute("target", "_blank")
    course = article.get_by_role("link", name="配套课程：", exact=False)
    expect(course).to_have_attribute("href", "/lesson/" + LESSON_ID)
    expect(article).to_contain_text("Python")
    course.click()
    page.wait_for_url("**/lesson/" + LESSON_ID)
    expect(
        page.get_by_role("heading", name=LESSONS[LESSON_ID]["title"], exact=True)
    ).to_be_visible()
    expect(practice(page)).to_contain_text("当前实践：Python")
    expect(evidence_card(page)).to_contain_text("当前记录：Python")
    expect_fields(page, {})
    expect(confirmation(page, "Python")).not_to_be_checked()
    download_zip(page)
    unchanged_except(original, stored(page))


def test_python_practice_and_evidence_are_explicit_preserved_and_reversible(page):
    original = seed(page, "/roadmap/agent")
    expect(group(page)).to_contain_text("1/1 课未记录")
    link = expand(page)
    expect(link).to_have_attribute("href", "/lesson/" + LESSON_ID + "#course-lab")
    link.click()
    page.wait_for_url("**/lesson/" + LESSON_ID + "#course-lab")
    expect(lab(page)).to_be_in_viewport()
    expect(page.get_by_role("button", name="取消完成标记", exact=True)).to_be_visible()
    expect(practice(page).get_by_role("button", name="标记 Python 实践完成")).to_be_disabled()
    fill(page, VALUES)
    after_evidence = stored(page)
    unchanged_except(original, after_evidence, "evidence")
    assert after_evidence["evidence"][0] == original["evidence"][0]
    own = [entry for entry in after_evidence["evidence"] if entry["lesson_id"] == LESSON_ID]
    assert len(own) == 1 and own[0]["language"] == "python"
    page.reload()
    page.wait_for_load_state("networkidle")
    expect_fields(page, VALUES)
    expect(confirmation(page, "Python")).not_to_be_checked()
    with page.expect_download() as event:
        evidence_card(page).get_by_role("button", name="下载实践证据 Markdown", exact=True).click()
    assert event.value.suggested_filename == LESSON_ID + "-python-evidence.md"
    markdown = Path(event.value.path()).read_text(encoding="utf-8")
    assert LESSON_ID in markdown and TITLE in markdown and "Python" in markdown
    assert all(value in markdown for value in VALUES.values())
    unchanged_except(after_evidence, stored(page))
    mark(page, "Python")
    after_mark = stored(page)
    assert after_mark["practice"][:-1] == original["practice"]
    assert after_mark["practice"][-1]["lesson_id"] == LESSON_ID
    assert after_mark["practice"][-1]["language"] == "python"
    unchanged_except(after_evidence, after_mark, "practice")
    goto(page, "/roadmap/agent")
    expect(group(page)).to_have_count(0)
    checklist(page).get_by_label("只看未记录实践", exact=True).uncheck()
    expect(group(page)).to_contain_text("0/1 课未记录")
    expand(page).click()
    page.wait_for_url("**/lesson/" + LESSON_ID + "#course-lab")
    practice(page).get_by_role("button", name="撤销 Python 实践记录", exact=True).click()
    assert stored(page)["practice"] == original["practice"]
    unchanged_except(after_evidence, stored(page))
    goto(page, "/roadmap/agent")
    expect(group(page)).to_contain_text("1/1 课未记录")
    unchanged_except(after_evidence, stored(page))


def test_mobile_keyboard_route_download_and_back_link_preserve_learning(page):
    page.set_viewport_size({"width": 375, "height": 812})
    original = seed(page, "/roadmap/agent")
    link = expand(page, keyboard=True)
    expect(link).to_have_attribute("href", "/lesson/" + LESSON_ID + "#course-lab")
    link.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/lesson/" + LESSON_ID + "#course-lab")
    expect(lab(page)).to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    download_zip(page, keyboard=True)
    back = page.get_by_role("link", name="返回 AI Agent", exact=False)
    back.scroll_into_view_if_needed()
    expect(back).to_be_in_viewport()
    directory = os.getenv("E2E_SCREENSHOT_DIR")
    if directory:
        lab(page).scroll_into_view_if_needed()
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / "deep-ai-agent-loop-course-mobile.png"))
    back.focus()
    page.keyboard.press("Enter")
    page.wait_for_url("**/roadmap/agent")
    expect(group(page)).to_contain_text("1/1 课未记录")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    unchanged_except(original, stored(page))
