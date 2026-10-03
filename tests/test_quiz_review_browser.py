"""Explicit quiz checks, review recovery, and unchanged learning records in real React."""

import copy
import json
import os
from pathlib import Path
from uuid import UUID

import pytest
from playwright.sync_api import expect, sync_playwright

from backend.curriculum import LESSONS, TRACKS

pytestmark = pytest.mark.e2e
KEY = "deep-ai-station:v1"
STAMP = "2026-10-04T01:02:03.000Z"
LESSON = "fullstack-http"
OTHER = "agent-agent-loop"


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        errors, paid = [], []
        context.on(
            "page", lambda opened: opened.on("pageerror", lambda error: errors.append(str(error)))
        )
        context.on(
            "request",
            lambda request: (
                paid.append(request.url)
                if any(
                    path in request.url
                    for path in ("/api/playground/run", "/api/playground/execute")
                )
                else None
            ),
        )
        try:
            yield context.new_page()
            assert not errors and not paid
        finally:
            context.close()
            browser.close()


def goto(page, path):
    page.goto(os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173") + path)
    expect(page.get_by_role("heading", level=1)).to_be_visible()


def stored(page):
    value = page.evaluate("key => localStorage.getItem(key)", KEY)
    return json.loads(value) if value else {}


def put(page, value, event=True):
    page.evaluate(
        """({key,value,event})=>{
      localStorage.setItem(key, JSON.stringify(value));
      if(event) window.dispatchEvent(new StorageEvent('storage',{key,storageArea:localStorage,newValue:JSON.stringify(value)}));
    }""",
        {"key": KEY, "value": value, "event": event},
    )


def review(lesson=LESSON):
    return {"lesson_id": lesson, "added_at": STAMP}


def quiz(page):
    return page.get_by_role("region", name="本课测验", exact=True)


def choose(page, lesson=LESSON, correct=False):
    index = LESSONS[lesson]["quiz"]["answer"]
    if not correct:
        index = (index + 1) % len(LESSONS[lesson]["quiz"]["options"])
    quiz(page).get_by_role("radio").nth(index).check()


def check(page, lesson=LESSON, correct=False):
    choose(page, lesson, correct)
    quiz(page).get_by_role("button", name="检查答案", exact=True).click()


def queue(page):
    return page.get_by_role("region", name="测验回顾", exact=True)


def queue_ids(page):
    return [item["lesson_id"] for item in stored(page).get("quizReview", [])]


def preferences(page):
    button = page.get_by_role("button", name="学习偏好与数据", exact=True)
    if not button.is_visible():
        page.get_by_role("button", name="打开导航", exact=True).click()
    button.click()
    return page.get_by_role("dialog")


def upload(page, data, accepted=True):
    before = stored(page)
    dialog = preferences(page)
    dialog.locator('input[type="file"]').set_input_files(
        {
            "name": "quiz-review.json",
            "mimeType": "application/json",
            "buffer": json.dumps(data, ensure_ascii=False).encode(),
        }
    )
    expect(dialog.get_by_role("status")).to_have_text(
        "学习记录已导入" if accepted else "学习记录格式不正确"
    )
    page.keyboard.press("Escape")
    current = stored(page)
    if accepted:
        epoch = current["history_reset_id"]
        assert str(UUID(epoch)) == epoch and UUID(epoch).version == 4
        assert epoch not in (before.get("history_reset_id"), data.get("history_reset_id"))
        assert current == {**data, "history_reset_id": epoch}
    else:
        assert current == before
    return current


def test_explicit_check_is_required_and_correctness_does_not_auto_complete(page):
    goto(page, f"/lesson/{LESSON}")
    initial = stored(page)
    submit = quiz(page).get_by_role("button", name="检查答案", exact=True)
    expect(submit).to_be_disabled()
    choose(page, correct=True)
    expect(quiz(page).locator(".quiz-result")).to_have_count(0)
    assert stored(page) == initial
    complete = page.get_by_role("button", name="标记本课完成", exact=True)
    for field in (
        page.get_by_role("region", name="本课验收", exact=True).get_by_role("checkbox").all()
    ):
        field.check()
    expect(complete).to_be_disabled()
    submit.focus()
    page.keyboard.press("Enter")
    expect(quiz(page)).to_contain_text("本题检查通过")
    expect(complete).to_be_enabled()
    assert stored(page)["completed"] == initial["completed"]
    choose(page, correct=False)
    expect(complete).to_be_disabled()
    choose(page, correct=True)
    expect(complete).to_be_disabled()
    submit.click()
    expect(complete).to_be_enabled()
    assert not queue_ids(page)
    complete.click()
    assert stored(page)["completed"] == [LESSON]


def test_wrong_check_keeps_first_time_other_progress_and_current_language(page):
    goto(page, f"/lesson/{LESSON}")
    original = stored(page)
    original.update(
        completed=[OTHER],
        notes={LESSON: "keep note"},
        bookmarks=["keep"],
        quizReview=[review(OTHER)],
    )
    put(page, original)
    choose(page)
    assert stored(page) == original
    quiz(page).get_by_role("button", name="检查答案", exact=True).click()
    after = stored(page)
    assert queue_ids(page) == [OTHER, LESSON]
    for key, value in original.items():
        if key != "quizReview":
            assert after[key] == value
    first = after["quizReview"][-1]
    quiz(page).get_by_role("button", name="检查答案", exact=True).click()
    assert stored(page)["quizReview"][-1] == first
    page.get_by_role("button", name="Go", exact=True).click()
    expect(quiz(page)).to_contain_text("再想一想")
    assert stored(page)["quizReview"] == after["quizReview"]
    check(page, correct=True)
    assert queue_ids(page) == [OTHER]
    for key in ("completed", "notes", "bookmarks", "runs", "resume"):
        assert stored(page)[key] == original[key]


def test_completed_lesson_can_be_reviewed_and_library_returns_to_quiz(page):
    goto(page, f"/lesson/{LESSON}")
    original = stored(page)
    original.update(completed=[LESSON], quizReview=[review(OTHER)])
    put(page, original)
    check(page)
    expect(page.get_by_role("button", name="取消完成标记", exact=True)).to_be_visible()
    page.reload()
    expect(quiz(page).get_by_role("button", name="检查答案", exact=True)).to_be_disabled()
    assert queue_ids(page) == [OTHER, LESSON]
    goto(page, "/library?tab=quiz-review")
    row = queue(page).get_by_role("article", name=LESSONS[LESSON]["title"], exact=True)
    row.get_by_role("link", name="回顾本课", exact=True).click()
    expect(quiz(page).get_by_role("button", name="检查答案", exact=True)).to_be_in_viewport()
    check(page, correct=True)
    assert queue_ids(page) == [OTHER]
    assert stored(page)["completed"] == [LESSON]
    goto(page, "/library?tab=quiz-review")
    expect(
        queue(page).get_by_role("article", name=LESSONS[LESSON]["title"], exact=True)
    ).to_have_count(0)
    expect(
        queue(page).get_by_role("article", name=LESSONS[OTHER]["title"], exact=True)
    ).to_be_visible()


def test_review_backup_round_trip_and_old_v1_reset(page):
    goto(page, f"/lesson/{LESSON}")
    check(page)
    preferences(page)
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    data = json.loads(Path(download.value.path()).read_text())
    assert data["version"] == 1 and len(data["quizReview"]) == 1
    assert set(data["quizReview"][0]) == {"lesson_id", "added_at"}
    page.keyboard.press("Escape")
    fresh = page.context.browser.new_context(viewport={"width": 375, "height": 812})
    try:
        restored = fresh.new_page()
        goto(restored, "/library")
        imported = upload(restored, data)
        restored.get_by_role("button", name="测验回顾", exact=True).click()
        expect(
            queue(restored).get_by_role("article", name=LESSONS[LESSON]["title"], exact=True)
        ).to_be_visible()
        assert stored(restored) == imported
        assert restored.evaluate("document.documentElement.scrollWidth <= innerWidth")
        legacy = {key: value for key, value in data.items() if key != "quizReview"}
        replacement = upload(restored, legacy)
        assert stored(restored) == replacement
        expect(queue(restored).get_by_role("article")).to_have_count(0)
    finally:
        fresh.close()


def test_full_capacity_unknown_records_can_be_removed_and_correct_check_frees_slot(page):
    goto(page, f"/lesson/{LESSON}")
    data = stored(page)
    data["quizReview"] = [review(f"fullstack-retired-{i}") for i in range(64)]
    put(page, data)
    check(page)
    assert len(queue_ids(page)) == 64 and LESSON not in queue_ids(page)
    expect(quiz(page)).to_contain_text("64")
    goto(page, "/library?tab=quiz-review")
    missing = page.get_by_role("region", name="不可用回顾记录", exact=True)
    expect(missing.get_by_role("article")).to_have_count(64)
    expect(missing.get_by_role("link")).to_have_count(0)
    missing.get_by_role("article", name="fullstack-retired-0", exact=True).get_by_role(
        "button", name="移除回顾记录", exact=True
    ).click()
    assert len(queue_ids(page)) == 63
    goto(page, f"/lesson/{LESSON}")
    check(page)
    assert len(queue_ids(page)) == 64 and LESSON in queue_ids(page)
    check(page, correct=True)
    assert len(queue_ids(page)) == 63 and LESSON not in queue_ids(page)


@pytest.mark.parametrize("invalid", ["duplicate", "extra", "date", "capacity"])
def test_invalid_review_import_never_overwrites_current_progress(page, invalid):
    goto(page, f"/lesson/{LESSON}")
    check(page)
    before = stored(page)
    candidate = copy.deepcopy(before)
    if invalid == "duplicate":
        candidate["quizReview"] *= 2
    elif invalid == "extra":
        candidate["quizReview"][0]["answer"] = 1
    elif invalid == "date":
        candidate["quizReview"][0]["added_at"] = "2026-02-30T00:00:00.000Z"
    else:
        candidate["quizReview"] = [review(f"fullstack-retired-{i}") for i in range(65)]
    upload(page, candidate, accepted=False)
    assert stored(page) == before


def test_pending_external_changes_are_kept_by_checks_and_library_removal(page):
    goto(page, f"/lesson/{LESSON}")
    initial = stored(page)
    newer = {**initial, "notes": {OTHER: "external note"}, "quizReview": [review(OTHER)]}
    put(page, newer, event=False)
    check(page)
    after = stored(page)
    assert queue_ids(page) == [OTHER, LESSON]
    assert after["notes"] == newer["notes"]
    page.evaluate(
        """({key,value})=>window.dispatchEvent(new StorageEvent('storage',{key,storageArea:localStorage,newValue:JSON.stringify(value)}))""",
        {"key": KEY, "value": initial},
    )
    assert queue_ids(page) == [OTHER, LESSON]
    check(page, correct=True)
    assert queue_ids(page) == [OTHER]
    goto(page, "/library?tab=quiz-review")
    latest = {
        **stored(page),
        "quizReview": [review(OTHER), review(LESSON)],
        "notes": {OTHER: "newer external note"},
    }
    put(page, latest, event=False)
    queue(page).get_by_role("article", name=LESSONS[OTHER]["title"], exact=True).get_by_role(
        "button", name="移除回顾记录", exact=True
    ).click()
    assert queue_ids(page) == [LESSON]
    assert stored(page)["notes"] == latest["notes"]


def test_storage_failure_keeps_unsaved_review_removal_for_export_and_recovery(page):
    goto(page, f"/lesson/{LESSON}")
    check(page)
    before = stored(page)
    page.evaluate("""() => {
      const original=Storage.prototype.setItem;
      window.restoreQuizStorage=()=>{Storage.prototype.setItem=original;delete window.restoreQuizStorage;};
      Storage.prototype.setItem=function(key,value){if(key==='deep-ai-station:v1')throw new DOMException('quota','QuotaExceededError');return original.call(this,key,value);};
    }""")
    check(page, correct=True)
    expect(page.get_by_role("alert")).to_contain_text("浏览器存储不可用")
    assert stored(page) == before
    put_event = "({key,value})=>window.dispatchEvent(new StorageEvent('storage',{key,storageArea:localStorage,newValue:JSON.stringify(value)}))"
    page.evaluate(put_event, {"key": KEY, "value": before})
    preferences(page)
    with page.expect_download() as download:
        page.get_by_role("button", name="导出学习记录", exact=True).click()
    backup = json.loads(Path(download.value.path()).read_text())
    assert backup["quizReview"] == []
    page.keyboard.press("Escape")
    page.evaluate("window.restoreQuizStorage()")
    check(page)
    expect(page.get_by_role("alert")).to_have_count(0)
    assert queue_ids(page) == [LESSON]
    page.reload()
    assert queue_ids(page) == [LESSON]


def test_same_id_quiz_revisions_never_revive_previous_checked_answer(page):
    base = os.getenv("E2E_BASE_URL", "http://127.0.0.1:5173")
    page.goto(base + "/tests/fixtures/quiz-review-harness.html")
    page.wait_for_function("typeof window.mountQuizReviewHarness === 'function'")
    page.evaluate(
        "input => window.mountQuizReviewHarness(input)", {"tracks": TRACKS, "lessonId": LESSON}
    )
    page.wait_for_function("typeof window.updateQuizReviewHarness === 'function'")
    expect(quiz(page)).to_be_visible()
    for field in (
        page.get_by_role("region", name="本课验收", exact=True).get_by_role("checkbox").all()
    ):
        field.check()
    complete = page.get_by_role("button", name="标记本课完成", exact=True)
    for field in ("question", "options", "answer", "explanation"):
        check(page, correct=True)
        expect(complete).to_be_enabled()
        revised = copy.deepcopy(TRACKS)
        lesson = next(
            item for track in revised for item in track["lessons"] if item["id"] == LESSON
        )
        if field == "options":
            lesson["quiz"][field] = list(reversed(lesson["quiz"][field]))
        elif field == "answer":
            lesson["quiz"][field] = (lesson["quiz"][field] + 1) % len(lesson["quiz"]["options"])
        else:
            lesson["quiz"][field] += " fixture revision"
        page.evaluate("tracks => window.updateQuizReviewHarness(tracks)", revised)
        expect(complete).to_be_disabled()
        expect(quiz(page).locator('input[type="radio"]:checked')).to_have_count(0)
        page.evaluate("tracks => window.updateQuizReviewHarness(tracks)", TRACKS)
        expect(quiz(page).get_by_role("button", name="检查答案", exact=True)).to_be_disabled()
        expect(complete).to_be_disabled()
        assert not stored(page).get("quizReview")
