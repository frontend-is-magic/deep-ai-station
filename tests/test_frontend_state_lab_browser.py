"""Shared React lab uses TypeScript facts without changing server-language preferences.

The platform runner owns services. These cases download the actual package but
never execute its code; the independent ZIP verifier exercises that application.
"""

import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from test_evidence_library_browser import evidence, index
from test_evidence_library_browser import row as evidence_row
from test_feed_course_browser import feed_payload
from test_lab_evidence_browser import FIELDS
from test_lab_evidence_browser import card as evidence_card
from test_practice_browser import practice
from test_progress_write_browser import EPOCH_A, goto
from test_progress_write_browser import page as page
from test_run_history_browser import export_record, progress, put_progress, record, stored

from backend.curriculum import LESSONS
from backend.feed import CURATED

pytestmark = pytest.mark.e2e
TITLE = "可运行 React 组件与状态实验"
LESSON_IDS = ("fullstack-components", "fullstack-jotai")
TIME = "2026-10-04T12:00:00.000Z"
LANGUAGES = (("typescript", "TypeScript"), ("go", "Go"), ("python", "Python"))
PUBLIC_PRACTICE = "公共前端实验实践记录"


def practice_record(lesson, language):
    return {"lesson_id": lesson, "language": language, "completed_at": TIME}


def baseline(language="go", **overrides):
    return progress(
        **{
            "history_reset_id": EPOCH_A,
            "language": language,
            "completed": ["fullstack-http"],
            "notes": {
                "fullstack-components": "保留组件课程原笔记",
                "agent-memory": "保留另一条路线笔记",
            },
            "bookmarks": ["jotai-guide"],
            "runs": [record(1)],
            "practice": [practice_record("fullstack-components", "go")],
            "evidence": [evidence("fullstack-routing", "python", command="保留已有 Python 证据")],
            **overrides,
        }
    )


def seed(page, value, path):
    goto(page, "/")
    put_progress(page, value)
    goto(page, path)


def lab(page):
    return page.get_by_role("region", name=TITLE, exact=True)


def public_practice(page):
    return page.get_by_role("region", name=PUBLIC_PRACTICE, exact=True)


def assert_other_records_unchanged(before, after, *changed):
    ignored = {"resume", *changed}
    assert {key: value for key, value in before.items() if key not in ignored} == {
        key: value for key, value in after.items() if key not in ignored
    }


def confirm_typescript(container):
    checkbox = container.get_by_role(
        "checkbox", name="我已用TypeScript运行成功与失败样例，并记录实际结果", exact=True
    )
    expect(checkbox).not_to_be_checked()
    expect(
        container.get_by_role("button", name="标记 TypeScript 实践完成", exact=True)
    ).to_be_disabled()
    checkbox.check()
    container.get_by_role("button", name="标记 TypeScript 实践完成", exact=True).click()
    expect(
        container.get_by_role("button", name="撤销 TypeScript 实践记录", exact=True)
    ).to_be_visible()


def practice_pairs(page):
    return {(item["lesson_id"], item["language"]) for item in stored(page).get("practice", [])}


def capture(page, filename):
    directory = os.getenv("E2E_SCREENSHOT_DIR")
    if directory:
        destination = Path(directory)
        destination.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination / filename))


@pytest.mark.parametrize("language,label", LANGUAGES)
def test_every_server_preference_downloads_the_same_real_typescript_package(page, language, label):
    original = baseline(language)
    seed(page, original, "/lesson/fullstack-components")
    expect(lab(page)).to_have_attribute("id", "course-lab")
    expect(practice(page)).to_contain_text("当前实践：" + label)
    expect(evidence_card(page)).to_contain_text("TypeScript")
    expect(public_practice(page)).to_have_count(0 if language == "typescript" else 1)
    if language != "typescript":
        expect(public_practice(page)).to_contain_text("当前实践：TypeScript")
    expect(page.get_by_role("button", name="标记 TypeScript 实践完成", exact=True)).to_have_count(1)
    link = lab(page).get_by_role("link", name="下载实验 · TypeScript", exact=True)
    expect(link).to_have_attribute("href", "/labs/frontend-state-typescript.zip")
    expect(lab(page).get_by_role("link", name="下载实验 · Go", exact=True)).to_have_count(0)
    expect(lab(page).get_by_role("link", name="下载实验 · Python", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="下载本课练习资料", exact=True)).to_have_attribute(
        "href", f"/api/lessons/fullstack-components/exercise.zip?language={language}"
    )
    before = stored(page)
    with page.expect_download() as event:
        link.click()
    assert event.value.suggested_filename == "frontend-state-typescript.zip"
    with zipfile.ZipFile(Path(event.value.path())) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["id"] == "frontend-state"
        assert manifest["languages"] == ["typescript"]
        assert manifest["lessons"] == list(LESSON_IDS)
        assert {"README.md", "CONTRACT.md", "EVIDENCE.md", "pnpm-lock.yaml", "package.json"} <= set(
            archive.namelist()
        )
        package = json.loads(archive.read("package.json"))
        assert {"react", "react-dom", "jotai"} <= set(package["dependencies"])
    assert stored(page) == before, "Download must not confirm practice or create evidence"
    assert_other_records_unchanged(original, stored(page))
    assert stored(page)["language"] == language
    assert "language=" not in page.url


def test_go_reference_and_two_typescript_lab_records_remain_independent_through_library(page):
    original = baseline()
    seed(page, original, "/lesson/fullstack-components")
    expect(
        practice(page).get_by_role("button", name="撤销 Go 实践记录", exact=True)
    ).to_be_visible()
    expect(public_practice(page)).to_contain_text("TypeScript · 未记录")
    commands = {
        "fullstack-components": "pnpm test；组件回调与 Props 的实际观察",
        "fullstack-jotai": "pnpm test；事实 atom 与筛选分母的实际观察",
    }
    for lesson_id in LESSON_IDS:
        goto(page, "/lesson/" + lesson_id)
        expect(practice(page)).to_contain_text("当前实践：Go")
        expect(evidence_card(page)).to_contain_text("TypeScript")
        expect(evidence_card(page).get_by_label(FIELDS["command"], exact=True)).to_have_value("")
        evidence_card(page).get_by_label(FIELDS["command"], exact=True).fill(commands[lesson_id])
        evidence_card(page).get_by_label(FIELDS["pending"], exact=True).fill(
            "这是用户填写，平台未执行验收。"
        )
        assert (lesson_id, "typescript") not in practice_pairs(page), (
            "Writing evidence is not practice confirmation"
        )
        confirm_typescript(public_practice(page))
        assert ("fullstack-components", "go") in practice_pairs(page)
        assert ("fullstack-jotai", "go") not in practice_pairs(page)
        assert stored(page)["language"] == "go"
    saved = stored(page)
    assert practice_pairs(page) == {
        ("fullstack-components", "go"),
        ("fullstack-components", "typescript"),
        ("fullstack-jotai", "typescript"),
    }
    assert saved["practice"][0] == original["practice"][0]
    assert_other_records_unchanged(original, saved, "evidence", "practice")
    own_evidence = [item for item in saved["evidence"] if item["lesson_id"] in LESSON_IDS]
    assert len(own_evidence) == 2
    assert {(item["lesson_id"], item["language"], item["command"]) for item in own_evidence} == {
        (lesson_id, "typescript", command) for lesson_id, command in commands.items()
    }
    assert saved["evidence"][0] == original["evidence"][0]
    assert export_record(page)["evidence"] == saved["evidence"]

    for lesson_id in LESSON_IDS:
        goto(page, "/library?tab=evidence")
        expect(index(page)).to_be_visible()
        entry = evidence_row(page, lesson_id, "typescript")
        link = entry.get_by_role(
            "link", name=f"打开课程：{LESSONS[lesson_id]['title']} · TypeScript", exact=True
        )
        expect(link).to_have_attribute("href", "/lesson/" + lesson_id)
        link.click()
        page.wait_for_url("**/lesson/" + lesson_id)
        expect(evidence_card(page).get_by_label(FIELDS["command"], exact=True)).to_have_value(
            commands[lesson_id]
        )
        expect(practice(page)).to_contain_text("当前实践：Go")
        expect(
            public_practice(page).get_by_role("button", name="撤销 TypeScript 实践记录", exact=True)
        ).to_be_visible()
        assert stored(page)["language"] == "go" and not urlparse(page.url).query
    goto(page, "/lesson/fullstack-components")
    practice(page).get_by_role("button", name="撤销 Go 实践记录", exact=True).click()
    assert practice_pairs(page) == {(lesson, "typescript") for lesson in LESSON_IDS}
    expect(
        public_practice(page).get_by_role("button", name="撤销 TypeScript 实践记录", exact=True)
    ).to_be_visible()
    public_practice(page).get_by_role("button", name="撤销 TypeScript 实践记录", exact=True).click()
    assert practice_pairs(page) == {("fullstack-jotai", "typescript")}
    assert stored(page)["evidence"] == saved["evidence"]
    assert_other_records_unchanged(original, stored(page), "practice", "evidence")


def test_jotai_reading_and_mobile_route_group_use_ts_practice_without_language_query(page):
    original = baseline(
        practice=[
            practice_record("fullstack-components", "go"),
            practice_record("fullstack-jotai", "go"),
            practice_record("fullstack-components", "typescript"),
        ]
    )
    page.route("**/api/feed*", lambda route: route.fulfill(json=feed_payload()))
    seed(page, original, "/feed")
    item = next(item for item in CURATED if item["id"] == "jotai-guide")
    article = page.get_by_role("article").filter(
        has=page.get_by_role("heading", name=item["title"], exact=True)
    )
    link = article.get_by_role("link", name="配套课程：", exact=False)
    expect(link).to_have_attribute("href", "/lesson/fullstack-jotai")
    expect(article).to_contain_text("公共前端 · Go 服务端参考")
    link.click()
    page.wait_for_url("**/lesson/fullstack-jotai")
    expect(lab(page)).to_be_visible()
    expect(practice(page)).to_contain_text("当前实践：Go")
    expect(public_practice(page)).to_contain_text("TypeScript · 未记录")
    assert stored(page)["language"] == "go"
    assert_other_records_unchanged(original, stored(page))

    page.set_viewport_size({"width": 375, "height": 812})
    goto(page, "/roadmap/fullstack")
    checklist = page.get_by_role("region", name="可运行实践清单", exact=True)
    group = checklist.locator('article[data-lab-id="frontend-state"]')
    expect(group).to_have_count(1)
    expect(group).to_have_attribute("data-language", "typescript")
    summary = group.get_by_label("查看关联课时：" + TITLE, exact=True)
    summary.focus()
    page.keyboard.press("Enter")
    expect(group.locator("details")).to_have_attribute("open", "")
    expect(group).to_contain_text("1/2 课未记录")
    expect(group.locator('li[data-lesson-id="fullstack-components"]')).to_contain_text("实践已记录")
    expect(group.locator('li[data-lesson-id="fullstack-jotai"]')).to_contain_text("实践未记录")
    for lesson_id in LESSON_IDS:
        expect(
            group.get_by_role(
                "link", name=f"进入实验：{LESSONS[lesson_id]['title']} · TypeScript", exact=True
            )
        ).to_have_attribute("href", f"/lesson/{lesson_id}#course-lab")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "deep-ai-frontend-state-route-mobile.png")
    group.get_by_role(
        "link", name=f"进入实验：{LESSONS['fullstack-jotai']['title']} · TypeScript", exact=True
    ).click()
    page.wait_for_url("**/lesson/fullstack-jotai#course-lab")
    expect(lab(page)).to_be_in_viewport()
    assert stored(page)["language"] == "go" and not urlparse(page.url).query
    public_practice(page).scroll_into_view_if_needed()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    capture(page, "deep-ai-frontend-state-practice-mobile.png")
    confirm_typescript(public_practice(page))
    assert practice_pairs(page) == {
        (lesson, language) for lesson in LESSON_IDS for language in ("go", "typescript")
    }
    goto(page, "/roadmap/fullstack")
    expect(checklist.locator('article[data-lab-id="frontend-state"]')).to_have_count(0)
    checklist.get_by_label("只看未记录实践", exact=True).uncheck()
    expect(group).to_have_count(1)
    expect(group).to_contain_text("0/2 课未记录")
    assert stored(page)["language"] == "go"
    assert_other_records_unchanged(original, stored(page), "practice")
