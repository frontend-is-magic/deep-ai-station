"""Discover runnable labs without turning visits or downloads into practice credit.

The shared runner owns services. These tests use actual curriculum/ZIP routes,
plus a small controlled catalog matrix; they never execute downloaded projects.
"""

import copy
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect
from test_course_labs_browser import LABS as FULLSTACK_LABS
from test_practice_browser import confirmation, mark, practice
from test_progress_write_browser import EPOCH_A, goto
from test_progress_write_browser import page as page
from test_run_history_browser import export_record, progress, put_progress, record, stored

from backend.curriculum import LESSONS

pytestmark = pytest.mark.e2e
FILTER = "只看未记录实践"
TIME = "2026-10-04T01:02:03.000Z"
MCP_TITLE = "可运行 MCP 只读协议实验"
LANGUAGES = (("typescript", "TypeScript"), ("go", "Go"), ("python", "Python"))


def practice_record(lesson_id, language):
    return {"lesson_id": lesson_id, "language": language, "completed_at": TIME}


def baseline(**values):
    return progress(
        **{
            "history_reset_id": EPOCH_A,
            "language": "go",
            "completed": ["agent-mcp"],
            "notes": {"fullstack-routing": "保留原有课程笔记"},
            "runs": [record(1)],
            "evidence": [
                {
                    "lesson_id": "fullstack-routing",
                    "language": "python",
                    "revision": "原证据版本",
                    "command": "",
                    "success": "",
                    "failure": "",
                    "pending": "未验证生产行为",
                    "updated_at": TIME,
                }
            ],
            **values,
        }
    )


def seed(page, value, track="fullstack"):
    goto(page, "/")
    put_progress(page, value)
    goto(page, "/roadmap/" + track)
    expect(checklist(page)).to_be_visible()


def checklist(page):
    return page.get_by_role("region", name="可运行实践清单", exact=True)


def group(page, lab_id="api-contract"):
    title = MCP_TITLE if lab_id == "mcp-readonly" else FULLSTACK_LABS[lab_id]["title"]
    return checklist(page).get_by_role("article", name=title, exact=True)


def expand(page, lab_id="api-contract", *, keyboard=False):
    current = group(page, lab_id)
    title = MCP_TITLE if lab_id == "mcp-readonly" else FULLSTACK_LABS[lab_id]["title"]
    details = current.locator("details")
    if details.get_attribute("open") is None:
        summary = current.get_by_label("查看关联课时：" + title, exact=True)
        if keyboard:
            summary.focus()
            page.keyboard.press("Enter")
        else:
            summary.click()
    expect(details).to_have_attribute("open", "")
    return current


def row(container, lesson_id):
    return container.locator(f'li[data-lesson-id="{lesson_id}"]')


def lesson_link(container, lesson_id, language):
    return row(container, lesson_id).get_by_role(
        "link", name=f"进入实验：{LESSONS[lesson_id]['title']} · {language}", exact=True
    )


def unchanged_except(before, after, *keys):
    omitted = set(keys)
    assert {key: value for key, value in before.items() if key not in omitted} == {
        key: value for key, value in after.items() if key not in omitted
    }


def assert_lab_destination(page, lesson_id, language, title):
    suffix = ("?language=" + language) if lesson_id.startswith("fullstack-") else ""
    page.wait_for_url("**/lesson/" + lesson_id + suffix + "#course-lab")
    url = urlparse(page.url)
    assert url.path == "/lesson/" + lesson_id and url.fragment == "course-lab"
    assert parse_qs(url.query) == (
        {"language": [language]} if lesson_id.startswith("fullstack-") else {}
    )
    section = page.get_by_role("region", name=title, exact=True)
    expect(section).to_have_attribute("id", "course-lab")
    expect(section).to_be_in_viewport()
    expect(page.get_by_label("课程笔记", exact=True)).to_be_visible()
    return section


def assert_zip(page, section, lab_id, language, label):
    link = section.get_by_role("link", name="下载实验 · " + label, exact=True)
    expect(link).to_have_attribute("href", f"/labs/{lab_id}-{language}.zip")
    before = stored(page)
    with page.expect_download() as event:
        link.click()
    assert event.value.suggested_filename == f"{lab_id}-{language}.zip"
    with zipfile.ZipFile(Path(event.value.path())) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["id"] == lab_id
        expected_entry = {
            "typescript": "src/server.ts",
            "go": "main.go",
            "python": "server.py" if lab_id == "mcp-readonly" else "app.py",
        }[language]
        assert expected_entry in archive.namelist()
        assert {"README.md", "CONTRACT.md", "EVIDENCE.md"} <= set(archive.namelist())
    assert stored(page) == before, "Downloading is not a practice confirmation"


def capture(page, name):
    directory = os.getenv("E2E_SCREENSHOT_DIR")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        checklist(page).scroll_into_view_if_needed()
        page.screenshot(path=str(destination / name))


def test_legacy_completed_route_still_offers_unrecorded_labs_and_filters_are_read_only(page):
    legacy = progress(
        language="go",
        completed=[lesson_id for lesson_id in LESSONS if lesson_id.startswith("fullstack-")],
        notes={"fullstack-routing": "旧 v1 笔记"},
    )
    seed(page, legacy)
    expect(page.locator(".overview-progress")).to_contain_text("24/24 已完成")
    expect(checklist(page).get_by_label(FILTER, exact=True)).to_be_checked()
    expect(checklist(page).get_by_role("article")).to_have_count(6)
    current = expand(page)
    expect(row(current, "fullstack-routing")).to_contain_text("实践未记录")
    expect(row(current, "fullstack-validation")).to_contain_text("实践未记录")
    capture(page, "deep-ai-lab-practice-desktop.png")
    expect(checklist(page).locator('a[href*="fullstack-integration"]')).to_have_count(0)
    expect(checklist(page).locator('a[href*="fullstack-launch"]')).to_have_count(0)
    checklist(page).get_by_label(FILTER, exact=True).uncheck()
    expect(checklist(page).get_by_role("article")).to_have_count(6)
    checklist(page).get_by_label(FILTER, exact=True).check()
    assert stored(page) == legacy and "practice" not in stored(page)
    assert export_record(page) == legacy
    page.reload()
    expect(checklist(page).get_by_label(FILTER, exact=True)).to_be_checked()
    expect(checklist(page).get_by_role("article")).to_have_count(6)
    assert stored(page) == legacy


def test_same_lab_keeps_both_lessons_with_independent_language_practice(page):
    value = baseline(
        practice=[
            practice_record("fullstack-routing", "go"),
            practice_record("fullstack-validation", "python"),
        ]
    )
    seed(page, value)
    for language, label in (("go", "Go"), ("python", "Python"), ("typescript", "TypeScript")):
        page.get_by_role("button", name=label, exact=True).click()
        current = expand(page)
        expect(current).to_have_attribute("data-language", language)
        expect(current.get_by_role("listitem")).to_have_count(2)
        expect(row(current, "fullstack-routing")).to_contain_text(
            "实践已记录" if language == "go" else "实践未记录"
        )
        expect(row(current, "fullstack-validation")).to_contain_text(
            "实践已记录" if language == "python" else "实践未记录"
        )
        for lesson in ("fullstack-routing", "fullstack-validation"):
            expect(lesson_link(current, lesson, label)).to_have_attribute(
                "href", f"/lesson/{lesson}?language={language}#course-lab"
            )
        unchanged_except(value, stored(page), "language")
    assert stored(page)["practice"] == value["practice"]


def test_each_fullstack_language_opens_actual_download_area_and_matching_zip(page):
    value = baseline()
    seed(page, value)
    for language, label in LANGUAGES:
        page.get_by_role("button", name=label, exact=True).click()
        current = expand(page)
        lesson_link(current, "fullstack-routing", label).click()
        section = assert_lab_destination(
            page, "fullstack-routing", language, FULLSTACK_LABS["api-contract"]["title"]
        )
        expect(practice(page)).to_contain_text("当前实践：" + label)
        assert_zip(page, section, "api-contract", language, label)
        assert not stored(page).get("practice")
        unchanged_except(value, stored(page), "language", "resume")
        page.go_back()
        expect(checklist(page)).to_be_visible()
        expect(row(expand(page), "fullstack-routing")).to_contain_text("实践未记录")


def test_agent_python_keyboard_download_confirmation_and_revocation_update_the_route(page):
    value = baseline(practice=[practice_record("fullstack-routing", "go")])
    seed(page, value, "agent")
    page.set_viewport_size({"width": 375, "height": 812})
    expect(checklist(page).get_by_role("article")).to_have_count(7)
    current = expand(page, "mcp-readonly", keyboard=True)
    expect(current).to_have_attribute("data-language", "python")
    expect(current).to_contain_text("实践未记录")
    expect(checklist(page).locator('a[href*="language=go"]')).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "deep-ai-lab-practice-mobile.png")
    link = lesson_link(current, "agent-mcp", "Python")
    expect(link).to_have_attribute("href", "/lesson/agent-mcp#course-lab")
    link.focus()
    page.keyboard.press("Enter")
    section = assert_lab_destination(page, "agent-mcp", "python", MCP_TITLE)
    assert_zip(page, section, "mcp-readonly", "python", "Python")
    assert stored(page)["language"] == "go" and stored(page)["practice"] == value["practice"]
    expect(confirmation(page, "Python")).not_to_be_checked()
    mark(page, "Python")
    assert {(item["lesson_id"], item["language"]) for item in stored(page)["practice"]} == {
        ("fullstack-routing", "go"),
        ("agent-mcp", "python"),
    }
    unchanged_except(value, stored(page), "resume", "practice")
    page.go_back()
    expect(checklist(page)).to_be_visible()
    expect(group(page, "mcp-readonly")).to_have_count(0)
    checkbox = checklist(page).get_by_label(FILTER, exact=True)
    checkbox.focus()
    page.keyboard.press("Space")
    expect(checkbox).not_to_be_checked()
    current = expand(page, "mcp-readonly", keyboard=True)
    expect(row(current, "agent-mcp")).to_contain_text("实践已记录")
    lesson_link(current, "agent-mcp", "Python").click()
    practice(page).get_by_role("button", name="撤销 Python 实践记录", exact=True).click()
    page.go_back()
    expect(row(expand(page, "mcp-readonly"), "agent-mcp")).to_contain_text("实践未记录")
    assert stored(page)["practice"] == value["practice"]
    assert stored(page)["language"] == "go"
    unchanged_except(value, stored(page), "resume", "practice")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_all_recorded_empty_state_can_show_history_without_claiming_other_languages(page):
    known_labs = {lesson for lab in FULLSTACK_LABS.values() for lesson in lab["lessons"]}
    value = baseline(
        practice=[practice_record(lesson, "go") for lesson in sorted(known_labs)]
        + [
            practice_record(lesson, "typescript")
            for lesson in ("fullstack-components", "fullstack-jotai")
        ]
    )
    seed(page, value)
    expect(checklist(page)).to_contain_text("当前语言的可运行实践均已记录")
    expect(checklist(page).get_by_role("article")).to_have_count(0)
    checklist(page).get_by_role("button", name="查看全部实践", exact=True).click()
    expect(checklist(page).get_by_label(FILTER, exact=True)).not_to_be_checked()
    expect(checklist(page).get_by_role("article")).to_have_count(6)
    expect(row(expand(page), "fullstack-routing")).to_contain_text("实践已记录")
    assert stored(page) == value
    page.get_by_role("button", name="Python", exact=True).click()
    checklist(page).get_by_label(FILTER, exact=True).check()
    expect(checklist(page).get_by_role("article")).to_have_count(5)
    expect(row(expand(page), "fullstack-routing")).to_contain_text("实践未记录")
    unchanged_except(value, stored(page), "language")


@pytest.mark.parametrize("fault", ["duplicate-id", "wrong-route", "unsupported-language"])
def test_inconsistent_catalog_never_generates_an_ambiguous_course_link(page, fault):
    def catalog(route):
        response = route.fetch()
        assert response.status == 200
        data = response.json()
        fullstack = next(track for track in data["tracks"] if track["id"] == "fullstack")
        lesson = next(item for item in fullstack["lessons"] if item["id"] == "fullstack-routing")
        if fault == "duplicate-id":
            agent = next(track for track in data["tracks"] if track["id"] == "agent")
            agent["lessons"].append(copy.deepcopy(lesson))
        elif fault == "wrong-route":
            lesson["track"] = "agent"
        else:
            fullstack["languages"] = ["python"]
        route.fulfill(response=response, json=data)

    page.route("**/api/curriculum", catalog)
    value = baseline()
    seed(page, value)
    if fault == "unsupported-language":
        expect(checklist(page)).to_contain_text("当前路线与语言暂无可运行实验")
        expect(checklist(page).get_by_role("article")).to_have_count(0)
        expect(
            checklist(page).get_by_role("button", name="查看全部实践", exact=True)
        ).to_have_count(0)
        assert stored(page) == value
        page.get_by_role("button", name="Python", exact=True).click()
        expect(lesson_link(expand(page), "fullstack-routing", "Python")).to_be_visible()
        unchanged_except(value, stored(page), "language")
    else:
        current = expand(page)
        expect(row(current, "fullstack-routing")).to_have_count(0)
        expect(lesson_link(current, "fullstack-validation", "Go")).to_be_visible()
        assert stored(page) == value
