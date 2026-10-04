"""Independently verify the fixed React/Jotai ZIP through its built browser UI.

Expected operations come from the frozen teaching contract, not product imports.
Only the maintainer's fixed ZIP and one explicit temporary mutation are executed.
"""

# ruff: noqa: S101 - independent assertions over fixed, public teaching data

import argparse
import hashlib
import json
import shutil
import stat
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from build_course_labs import DESTINATION, ROOT
from verify_course_labs import ensure_listener_closed
from verify_upload_client import (
    LAUNCHER,
    checked_command,
    environment,
    free_port,
    ready,
    stop_client,
)

KEY = "frontend-state-lab:v1"
OTHER_KEY = "deep-ai-station:v1"
OTHER_VALUE = "fixed-public-platform-key-sentinel"
TITLES = {
    "lesson-1": "用 Props 展示课程卡",
    "lesson-2": "用事件更新事实",
    "lesson-3": "用派生 atom 计算视图",
}
IDS = ["lesson-1", "lesson-2", "lesson-3"]
MEMBERS = frozenset(
    (
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "vite.config.ts",
        "index.html",
        "src/main.tsx",
        "src/App.tsx",
        "src/LessonCard.tsx",
        "src/state.ts",
        "src/state.test.ts",
        "src/style.css",
        "src/components/ui/button.tsx",
        "src/lib/utils.ts",
        "README.md",
        "CONTRACT.md",
        "EVIDENCE.md",
        "AGENTS.md",
        ".gitignore",
        "manifest.json",
        ".prettierrc.json",
    )
)
BAD_NOTICE = "本地记录格式无效，未采用；本页先使用空事实，操作后可覆盖。"
READ_NOTICE = "浏览器存储不可用，本页仅使用内存；刷新可能丢失。"
WRITE_NOTICE = "写入浏览器存储失败，已保留本页内存；刷新可能丢失。"
NORMAL_NOTICE = "只保存完成与收藏事实；筛选和统计由本页派生。"
MUTATION = "get(factsAtom).completedIds.length"

# Frozen manually before reading the implementation; no product function computes
# these answers. Each row is action, completed IDs, favorite IDs, filter,
# visible IDs, global completed/favorite/percent, and this document's write count.
# Also preserved in /private/tmp/deep-ai-frontend-state-oracle.json for review.
MANUAL = (
    (None, [], [], "all", IDS, 0, 0, 0, 0),
    (("favorite", "lesson-3", True), [], ["lesson-3"], "all", IDS, 0, 1, 0, 1),
    (("complete", "lesson-2", True), ["lesson-2"], ["lesson-3"], "all", IDS, 1, 1, 33, 2),
    (
        ("complete", "lesson-1", True),
        ["lesson-1", "lesson-2"],
        ["lesson-3"],
        "all",
        IDS,
        2,
        1,
        67,
        3,
    ),
    (
        ("complete", "lesson-1", True),
        ["lesson-1", "lesson-2"],
        ["lesson-3"],
        "all",
        IDS,
        2,
        1,
        67,
        3,
    ),
    (
        ("filter", "pending"),
        ["lesson-1", "lesson-2"],
        ["lesson-3"],
        "pending",
        ["lesson-3"],
        2,
        1,
        67,
        3,
    ),
    (
        ("filter", "favorites"),
        ["lesson-1", "lesson-2"],
        ["lesson-3"],
        "favorites",
        ["lesson-3"],
        2,
        1,
        67,
        3,
    ),
    (("filter", "all"), ["lesson-1", "lesson-2"], ["lesson-3"], "all", IDS, 2, 1, 67, 3),
    (
        ("favorite", "lesson-1", True),
        ["lesson-1", "lesson-2"],
        ["lesson-1", "lesson-3"],
        "all",
        IDS,
        2,
        2,
        67,
        4,
    ),
    (
        ("complete", "lesson-2", False),
        ["lesson-1"],
        ["lesson-1", "lesson-3"],
        "all",
        IDS,
        1,
        2,
        33,
        5,
    ),
    (
        ("complete", "lesson-3", True),
        ["lesson-1", "lesson-3"],
        ["lesson-1", "lesson-3"],
        "all",
        IDS,
        2,
        2,
        67,
        6,
    ),
    (("complete", "lesson-2", True), IDS, ["lesson-1", "lesson-3"], "all", IDS, 3, 2, 100, 7),
    (("filter", "pending"), IDS, ["lesson-1", "lesson-3"], "pending", [], 3, 2, 100, 7),
    (("show-all",), IDS, ["lesson-1", "lesson-3"], "all", IDS, 3, 2, 100, 7),
    (
        ("filter", "favorites"),
        IDS,
        ["lesson-1", "lesson-3"],
        "favorites",
        ["lesson-1", "lesson-3"],
        3,
        2,
        100,
        7,
    ),
    (("reload",), IDS, ["lesson-1", "lesson-3"], "all", IDS, 3, 2, 100, 0),
)


class BehaviorMismatch(AssertionError):
    """Only this precise rendered-value failure can reject the fixed mutant."""

    def __init__(self, check):
        super().__init__(f"Rendered contract mismatch: {check}")
        self.check = check


def extract_fixed_archive(archive, folder):
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        names = [item.filename for item in entries]
        if len(names) != len(MEMBERS) or set(names) != MEMBERS:
            raise RuntimeError("Unexpected, missing, or duplicate frontend ZIP members")
        if (
            any(
                item.is_dir()
                or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG)
                or item.file_size > 2_000_000
                for item in entries
            )
            or sum(item.file_size for item in entries) > 5_000_000
        ):
            raise RuntimeError("Unsafe frontend ZIP member")
        content = {name: package.read(name) for name in names}
    manifest = json.loads(content["manifest.json"])
    if manifest != {
        "id": "frontend-state",
        "version": "frontend-state-v1",
        "lessons": ["fullstack-components", "fullstack-jotai"],
        "languages": ["typescript"],
    }:
        raise RuntimeError("Unexpected frontend manifest")
    for name, body in content.items():
        target = folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    return content


def make_wrong_count(source):
    if source.count(MUTATION) != 1:
        raise RuntimeError("Fixed mutation target missing or ambiguous")
    return source.replace(MUTATION, MUTATION + " + 1", 1)


def permitted_request(base, url, method, resource):
    target, origin = urlsplit(url), urlsplit(base)
    return (
        target.scheme == origin.scheme
        and target.netloc == origin.netloc
        and not target.username
        and not target.password
        and method == "GET"
        and resource in {"document", "script", "stylesheet", "image", "font", "other"}
        and (target.path in {"/", "/favicon.ico"} or target.path.startswith("/assets/"))
    )


@contextmanager
def preview(folder, env, counters):
    import subprocess

    port = free_port()
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(  # noqa: S603 - fixed maintainer preview launcher, no user argv
            [shutil.which("node"), str(folder / ".verify-vite.mjs")],
            cwd=folder,
            env={**env, "PORT": str(port)},
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        counters["preview_started"] += 1
        try:
            ready(port, process, api=False)
            yield f"http://127.0.0.1:{port}"
        finally:
            failures = []
            try:
                stop_client(process)
            except Exception as error:
                failures.append(error)
            try:
                ensure_listener_closed(port)
            except Exception as error:
                failures.append(error)
            log.seek(0)
            if log.read(4097).strip():
                failures.append(RuntimeError("Unexpected frontend preview diagnostics"))
            if failures:
                raise RuntimeError("Owned frontend preview cleanup failed") from failures[0]
            counters["preview_reaped"] += 1


def init_storage(seed, *, read_failure=False, write_failure=False):
    options = json.dumps(
        {
            "key": KEY,
            "other": OTHER_KEY,
            "sentinel": OTHER_VALUE,
            "seed": seed,
            "readFailure": read_failure,
            "writeFailure": write_failure,
        }
    )
    return """(() => {
      const options = OPTIONS;
      const get = Storage.prototype.getItem, set = Storage.prototype.setItem;
      if (get.call(localStorage, options.other) === null) {
        set.call(localStorage, options.other, options.sentinel);
        if (options.seed !== null) set.call(localStorage, options.key, options.seed);
      }
      window.__stateAudit = {writes:[], rawGet:key=>get.call(localStorage,key)};
      Storage.prototype.getItem = function(key) {
        if (this===localStorage && key===options.key && options.readFailure)
          throw new DOMException('fixed-read-sentinel', 'SecurityError');
        return get.call(this,key);
      };
      Storage.prototype.setItem = function(key,value) {
        window.__stateAudit.writes.push({area:this===localStorage?'local':'session',key});
        if (this===localStorage && key===options.key && options.writeFailure)
          throw new DOMException('fixed-write-sentinel', 'QuotaExceededError');
        return set.call(this,key,value);
      };
    })();""".replace("OPTIONS", options)


@contextmanager
def page_case(browser, base, *, seed=None, read_failure=False, write_failure=False, mobile=False):
    from playwright.sync_api import expect

    context = browser.new_context(
        viewport={"width": 375 if mobile else 1280, "height": 900}, service_workers="block"
    )
    page = context.new_page()
    page.set_default_timeout(5000)
    audit = {"documents": 0, "expected_documents": 1, "network": [], "ws": [], "errors": []}
    page.on(
        "framenavigated",
        lambda frame: (
            audit.update(documents=audit["documents"] + 1) if frame == page.main_frame else None
        ),
    )
    page.on("pageerror", lambda _error: audit["errors"].append("pageerror"))
    page.on(
        "console",
        lambda message: audit["errors"].append("console") if message.type == "error" else None,
    )

    def route_request(route):
        request = route.request
        if permitted_request(base, request.url, request.method, request.resource_type):
            route.continue_()
        else:
            audit["network"].append("non-static-request")
            route.abort()

    def socket_attempt(socket):
        audit["ws"].append("websocket")
        socket.close()

    context.route("**/*", route_request)
    context.route_web_socket("**", socket_attempt)
    page.add_init_script(init_storage(seed, read_failure=read_failure, write_failure=write_failure))
    try:
        page.goto(base)
        expect(page.get_by_role("heading", name="组件与派生状态实验", exact=True)).to_be_visible()
        scripts = page.locator('script[type="module"][src]').evaluate_all(
            "nodes=>nodes.map(node=>new URL(node.src).pathname)"
        )
        assert scripts and all(name.startswith("/assets/") for name in scripts)
        yield page, audit
    finally:
        try:
            assert audit["documents"] == audit["expected_documents"], (
                "Unexpected document navigation"
            )
            assert not audit["network"] and not audit["ws"] and not audit["errors"]
            assert page.evaluate("key=>window.__stateAudit.rawGet(key)", OTHER_KEY) == OTHER_VALUE
            assert page.evaluate("Object.keys(localStorage).sort()") in (
                [OTHER_KEY],
                sorted([OTHER_KEY, KEY]),
            )
            assert page.evaluate("sessionStorage.length") == 0
            assert page.evaluate("indexedDB.databases().then(items=>items.length)") == 0
            assert all(
                item == {"area": "local", "key": KEY}
                for item in page.evaluate("window.__stateAudit.writes")
            )
        finally:
            context.close()


def card(page, identifier):
    return page.get_by_role("article", name=TITLES[identifier], exact=True)


def change(page, kind, identifier, value):
    card(page, identifier).get_by_role(
        "checkbox", name="已完成" if kind == "complete" else "已收藏", exact=True
    ).set_checked(value)


def reload_page(page, audit):
    audit["expected_documents"] += 1
    page.reload()
    from playwright.sync_api import expect

    expect(page.get_by_role("heading", name="组件与派生状态实验", exact=True)).to_be_visible()


def assert_statistic(page, label, value, check):
    from playwright.sync_api import expect

    locator = page.get_by_label(label, exact=True)
    expect(locator).to_be_visible()
    try:
        expect(locator).to_have_text(value, timeout=1500)
    except AssertionError as error:
        raise BehaviorMismatch(check) from error


def assert_view(page, completed, favorites, selected, visible, count, favorite_count, percent):
    from playwright.sync_api import expect

    assert_statistic(page, "总完成数", f"{count} / 3", "completed_count")
    assert_statistic(page, "总收藏数", f"{favorite_count} / 3", "favorite_count")
    assert_statistic(page, "全局完成率", f"{percent}%", "completion_percent")
    expect(page.get_by_label("显示课程", exact=True)).to_have_value(selected)
    region = page.get_by_role("region", name="课程卡片", exact=True)
    expect(region.get_by_role("article")).to_have_count(len(visible))
    assert (
        region.get_by_role("article").evaluate_all("nodes=>nodes.map(node=>node.dataset.cardId)")
        == visible
    )
    for identifier in visible:
        for name, expected in (
            ("已完成", identifier in completed),
            ("已收藏", identifier in favorites),
        ):
            expect(
                card(page, identifier).get_by_role("checkbox", name=name, exact=True)
            ).to_be_checked(checked=expected)
    if not visible:
        expect(region.get_by_text("当前筛选没有课程", exact=True)).to_be_visible()
    facts = json.loads(page.get_by_label("持久事实 JSON", exact=True).inner_text())
    assert type(facts["version"]) is int and facts == {
        "version": 1,
        "completedIds": completed,
        "favoriteIds": favorites,
    }
    assert json.loads(page.get_by_label("派生视图 JSON", exact=True).inner_text()) == {
        "filter": selected,
        "visibleIds": visible,
        "completedCount": count,
        "favoriteCount": favorite_count,
        "completionPercent": percent,
    }


def raw_storage(page):
    return page.evaluate("key=>window.__stateAudit.rawGet(key)", KEY)


def assert_facts(page, completed, favorites):
    facts = json.loads(raw_storage(page))
    assert type(facts["version"]) is int and facts == {
        "version": 1,
        "completedIds": completed,
        "favoriteIds": favorites,
    }


def core_behavior(page, audit):
    from playwright.sync_api import expect

    for index, (
        action,
        completed,
        favorites,
        selected,
        visible,
        count,
        fcount,
        percent,
        writes,
    ) in enumerate(MANUAL):
        if action:
            if action[0] in {"complete", "favorite"}:
                change(page, *action)
            elif action[0] == "filter":
                page.get_by_label("显示课程", exact=True).select_option(action[1])
            elif action[0] == "show-all":
                page.get_by_role("button", name="显示全部课程", exact=True).click()
            else:
                reload_page(page, audit)
        assert_view(page, completed, favorites, selected, visible, count, fcount, percent)
        expect(page.locator('p[role="status"]')).to_have_text(NORMAL_NOTICE)
        assert len(page.evaluate("window.__stateAudit.writes")) == writes
        if index == 0:
            assert raw_storage(page) is None
        else:
            assert_facts(page, completed, favorites)
    return len(MANUAL)


def storage_behaviors(browser, base):
    from playwright.sync_api import expect

    reversed_seed = (
        '{"version":1,"completedIds":["lesson-3","lesson-1"],"favoriteIds":["lesson-2"]}'
    )
    with page_case(browser, base, seed=reversed_seed) as (page, _audit):
        assert_view(page, ["lesson-1", "lesson-3"], ["lesson-2"], "all", IDS, 2, 1, 67)
        assert (
            raw_storage(page) == reversed_seed and page.evaluate("window.__stateAudit.writes") == []
        )
        page.get_by_label("显示课程", exact=True).select_option("pending")
        assert_view(page, ["lesson-1", "lesson-3"], ["lesson-2"], "pending", ["lesson-2"], 2, 1, 67)
        assert (
            raw_storage(page) == reversed_seed and page.evaluate("window.__stateAudit.writes") == []
        )
        # Completion removes this card from the pending filter immediately.
        # A single native click must not wait for the removed checkbox afterward.
        card(page, "lesson-2").get_by_role("checkbox", name="已完成", exact=True).click()
        assert_view(page, IDS, ["lesson-2"], "pending", [], 3, 1, 100)
        assert_facts(page, IDS, ["lesson-2"])
        assert len(page.evaluate("window.__stateAudit.writes")) == 1

    bad = '{"version":1,"completedIds":["lesson-1"],"favoriteIds":[],"completedCount":"fixed-bad-record-sentinel"}'
    with page_case(browser, base, seed=bad) as (page, _audit):
        assert_view(page, [], [], "all", IDS, 0, 0, 0)
        expect(page.locator('p[role="status"]')).to_have_text(BAD_NOTICE)
        expect(page.locator("body")).not_to_contain_text("fixed-bad-record-sentinel")
        assert raw_storage(page) == bad and page.evaluate("window.__stateAudit.writes") == []
        change(page, "complete", "lesson-1", True)
        assert_facts(page, ["lesson-1"], [])
        expect(page.locator('p[role="status"]')).to_have_text(NORMAL_NOTICE)

    with page_case(browser, base, read_failure=True, write_failure=True) as (page, audit):
        assert_view(page, [], [], "all", IDS, 0, 0, 0)
        expect(page.locator('p[role="status"]')).to_have_text(READ_NOTICE)
        change(page, "favorite", "lesson-3", True)
        assert_view(page, [], ["lesson-3"], "all", IDS, 0, 1, 0)
        expect(page.locator('p[role="status"]')).to_have_text(WRITE_NOTICE)
        assert raw_storage(page) is None
        reload_page(page, audit)
        assert_view(page, [], [], "all", IDS, 0, 0, 0)
        expect(page.locator('p[role="status"]')).to_have_text(READ_NOTICE)
        expect(page.locator("body")).not_to_contain_text("fixed-read-sentinel")
        expect(page.locator("body")).not_to_contain_text("fixed-write-sentinel")

    old = '{"version":1,"completedIds":["lesson-2"],"favoriteIds":[]}'
    with page_case(browser, base, seed=old, write_failure=True) as (page, audit):
        assert_view(page, ["lesson-2"], [], "all", IDS, 1, 0, 33)
        change(page, "complete", "lesson-1", True)
        assert_view(page, ["lesson-1", "lesson-2"], [], "all", IDS, 2, 0, 67)
        expect(page.locator('p[role="status"]')).to_have_text(WRITE_NOTICE)
        assert raw_storage(page) == old
        reload_page(page, audit)
        assert_view(page, ["lesson-2"], [], "all", IDS, 1, 0, 33)
    return [
        "unordered_restore_no_initial_write",
        "invalid_record_explicit_replacement",
        "unavailable_storage_memory_only",
        "write_failure_keeps_previous_persistence",
    ]


def keyboard_mobile(browser, base, screenshots):
    from playwright.sync_api import expect

    with page_case(browser, base, mobile=True) as (page, _audit):
        # Reach the real native controls by keyboard; no dispatchEvent or store access.
        first = card(page, "lesson-1").get_by_role("checkbox", name="已完成", exact=True)
        for _ in range(12):
            page.keyboard.press("Tab")
            if first.evaluate("node=>node===document.activeElement"):
                break
        expect(first).to_be_focused()
        page.keyboard.press("Space")
        assert_view(page, ["lesson-1"], [], "all", IDS, 1, 0, 33)
        page.keyboard.press("Tab")
        favorite = card(page, "lesson-1").get_by_role("checkbox", name="已收藏", exact=True)
        expect(favorite).to_be_focused()
        page.keyboard.press("Space")
        assert_view(page, ["lesson-1"], ["lesson-1"], "all", IDS, 1, 1, 33)
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Shift+Tab")
        selector = page.get_by_label("显示课程", exact=True)
        expect(selector).to_be_focused()
        # Native select type-ahead works without a popup in macOS headless Chromium.
        # Playwright press() has no Unicode key mapping; CDP sends trusted keyboard
        # input for the visible option's first character without setting the value.
        keyboard = page.context.new_cdp_session(page)
        try:
            keyboard.send(
                "Input.dispatchKeyEvent",
                {"type": "keyDown", "key": "收", "text": "收", "unmodifiedText": "收"},
            )
            keyboard.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": "收"})
        finally:
            keyboard.detach()
        expect(selector).to_be_focused()
        assert_view(page, ["lesson-1"], ["lesson-1"], "favorites", ["lesson-1"], 1, 1, 33)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        expect(page.get_by_role("region", name="事实与派生值", exact=True)).to_be_visible()
        if screenshots:
            page.screenshot(path=str(screenshots / "frontend-state-mobile.png"), full_page=True)
    return True


def verify(screenshots=None):
    from playwright.sync_api import sync_playwright

    archive = DESTINATION / "frontend-state-typescript.zip"
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    counters = {"preview_started": 0, "preview_reaped": 0, "package_commands_waited": 0}
    env = environment()
    temp_root = "/private/tmp" if Path("/private/tmp").is_dir() else None
    with tempfile.TemporaryDirectory(prefix="deep-ai-frontend-state-", dir=temp_root) as temporary:
        root = Path(temporary).resolve()
        if root.is_relative_to(ROOT):
            raise RuntimeError("Verification must be outside the checkout")
        normal = root / "normal"
        original = extract_fixed_archive(archive, normal)
        for args in (["pnpm", "install", "--frozen-lockfile"], ["pnpm", "check"]):
            checked_command(args, normal, env)
            counters["package_commands_waited"] += 1
        (normal / ".verify-vite.mjs").write_text(LAUNCHER, encoding="utf-8")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                with preview(normal, env, counters) as base:
                    with page_case(browser, base) as (page, audit):
                        steps = core_behavior(page, audit)
                        if screenshots:
                            page.screenshot(
                                path=str(screenshots / "frontend-state-desktop.png"), full_page=True
                            )
                    storage = storage_behaviors(browser, base)
                    mobile = keyboard_mobile(browser, base, screenshots)
                assert all((normal / name).read_bytes() == body for name, body in original.items())
                mutant = root / "wrong-count"
                shutil.copytree(
                    normal,
                    mutant,
                    ignore=shutil.ignore_patterns("node_modules", "dist", ".verify-vite.mjs"),
                )
                (mutant / "node_modules").symlink_to(
                    normal / "node_modules", target_is_directory=True
                )
                target = mutant / "src/state.ts"
                target.write_text(
                    make_wrong_count(target.read_text(encoding="utf-8")), encoding="utf-8"
                )
                checked_command(["pnpm", "exec", "vite", "build"], mutant, env)
                counters["package_commands_waited"] += 1
                (mutant / ".verify-vite.mjs").write_text(LAUNCHER, encoding="utf-8")
                rejected = None
                with preview(mutant, env, counters) as base:
                    try:
                        with page_case(browser, base) as (page, audit):
                            core_behavior(page, audit)
                    except BehaviorMismatch as mismatch:
                        rejected = mismatch.check
                    assert rejected == "completed_count", (
                        "Wrong derivation escaped the same core assertion"
                    )
            finally:
                browser.close()
        assert counters["preview_started"] == counters["preview_reaped"] == 2
        assert all((normal / name).read_bytes() == body for name, body in original.items())
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash
    print(
        json.dumps(
            {
                "lab": "frontend-state-v1",
                "archive_sha256": archive_hash,
                "archive_members": len(MEMBERS),
                "normal": {
                    "core_steps": steps,
                    "storage_behaviors": storage,
                    "mobile_keyboard": mobile,
                },
                "mutation": {
                    "temporary_only": True,
                    "normal_passed_first": True,
                    "change": "completed_count_plus_one",
                    "rejected_by": rejected,
                },
                "serving": "built_dist_no_hmr",
                "business_requests": 0,
                "websockets": 0,
                "unrelated_storage_key_unchanged": True,
                "archive_unchanged": True,
                "headless_closed": True,
                "listeners_released": True,
                **counters,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots-dir", type=Path)
    args = parser.parse_args()
    screenshots = args.screenshots_dir.resolve() if args.screenshots_dir else None
    if screenshots:
        if screenshots.is_relative_to(ROOT):
            parser.error("Screenshot output must be outside the checkout")
        screenshots.mkdir(parents=True, exist_ok=True)
    verify(screenshots)


if __name__ == "__main__":
    main()
