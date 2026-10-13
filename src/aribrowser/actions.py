"""요소 ID를 실제 Chromium 입력으로 바꾸고 조작 전후의 의미 차이와 검증 결과를 반환한다.

v0.1.0의 조작 응답은 세 단계를 구분한다.
- `action_dispatched`: Playwright가 입력을 실제로 전달했다(오류 없이 완료).
- `event_received`: 페이지가 해당 DOM 이벤트를 실제로 받았다. 조작 직전에 요소에
  설치한 캡처 단계 리스너(activity.js의 probe)가 근거다. 문서가 교체되어 읽을 수
  없으면 null이며, 그 경우 `navigation`으로 탐색 발생 여부를 알 수 있다.
- `expected_state_reached`: Agent가 `expect`로 지정한 기대 상태(URL/텍스트/선택자/
  값/네트워크 안정 등)에 도달했다. 도달하지 못하면 `EXPECTATION_NOT_MET` 오류로
  원인을 돌려주어 미완료 작업을 성공으로 오인하지 않게 한다.

v0.2.0의 `navigate`는 네 번째 단계 `content_ready`를 추가한다. `load` 뒤에 readiness.py가
본문 텍스트·스켈레톤·보안 문자·로그인 폼 신호로 페이지가 실제 내용을 담았는지 판정하고,
빈 껍데기(`empty`)는 한 번 새로 고쳐 재시도한 뒤에도 비어 있으면 `PAGE_EMPTY`, 차단은
`PAGE_BLOCKED` 오류로 돌려준다. `include`로 레코드/상품/상태를 같은 응답에 실어 왕복을 줄인다.

v0.3.0의 `navigate`는 `mode="minimal"`을 추가한다. 사이트 구조를 이미 아는 Agent가 `include`로 구조화
결과만 원할 때 요약(영역/제목/역할 수/검색창)과 관측 자체를 생략하고 URL·제목·HTTP 상태·readiness·
verification·include 결과·오류·budget만 돌려준다. 실행 엔진 정보(`runtime`)는 첫 응답에 전체를, 이후에는
`runtime_id`만 싣는다.
"""

import asyncio
import uuid
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .budget import budget_report, measure, parse_limits
from .errors import BrowserError
from .readiness import HINTS, assess, parse_ready
from .summary import minimal_view
from .waiting import baseline_of, parse_conditions, parse_timeout, wait_for

ELEMENT_ACTIONS = {"click", "open_link", "double_click", "hover", "fill", "type", "clear", "check",
                   "uncheck", "select", "upload", "submit", "scroll_into_view", "close_modal", "drag_drop"}
PAGE_ACTIONS = {"back", "forward", "reload", "press", "scroll", "coordinate_click"}
NAVIGATION_ACTIONS = {"back", "forward", "reload"}

# 조작별로 페이지가 받아야 하는 DOM 이벤트. 하나라도 관측되면 event_received=true다.
PROBE_EVENTS = {
    "click": ["click", "mousedown", "pointerdown"], "open_link": ["click", "mousedown", "pointerdown"],
    "double_click": ["dblclick", "click"], "hover": ["mouseover", "pointerover", "mouseenter"],
    "fill": ["input", "change"], "type": ["input", "keydown"], "clear": ["input", "change"],
    "check": ["change", "click"], "uncheck": ["change", "click"], "select": ["change", "input"],
    "upload": ["change", "input"], "submit": ["submit"], "close_modal": ["close", "cancel", "keydown"],
    "drag_drop": ["dragstart", "dragend", "pointerdown", "mousedown"], "press": ["keydown", "keyup"],
    "coordinate_click": ["click", "mousedown", "pointerdown"], "scroll": ["scroll", "wheel"],
    "scroll_into_view": ["scroll"],
}

# 조작 직전 요소(또는 문서)에 일회성 캡처 리스너를 설치한다. submit은 소속 폼에서 듣는다.
PROBE_INSTALL = """(el, [token, names]) => {
  const activity = window.__aribrowser_activity__;
  if (!activity) return false;
  let target = el;
  if (names.includes('submit') && !(el instanceof HTMLFormElement)) target = el.form || el.closest('form') || el;
  if (names.includes('scroll') && !(el instanceof HTMLFormElement)) target = document;
  activity.probe(target, token, names);
  return true;
}"""
PROBE_INSTALL_DOCUMENT = """([token, names]) => {
  const activity = window.__aribrowser_activity__;
  if (!activity) return false;
  activity.probe(document, token, names);
  return true;
}"""
PROBE_READ = "token => window.__aribrowser_activity__ ? window.__aribrowser_activity__.read(token) : null"


def validate_url(url: str) -> str:
    """브라우징 URL 스킴을 제한하고 javascript:를 통한 명령 코드 실행을 차단한다."""
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https", "file", "about", "data"}:
        raise BrowserError("INVALID_ARGUMENT", "http/https/file/about/data URL이 필요합니다.")
    return url


async def settle(page, milliseconds: int = 150) -> None:
    """이벤트 루프와 DOM 갱신에 짧은 시간을 주되 영구 네트워크 연결을 기다리지 않는다."""
    if type(milliseconds) is not int or not 0 <= milliseconds <= 5000:
        raise BrowserError("INVALID_ARGUMENT", "settle_ms는 0~5000 정수입니다.")
    await asyncio.sleep(milliseconds / 1000)


# navigate/action 응답을 만들 때 snapshot으로 그대로 넘기는 출력 옵션.
VIEW_OPTIONS = {"format", "fields", "compact", "max_bytes", "max_elements", "max_tokens", "limit", "offset",
                "include_hidden", "region", "max_regions", "max_headings", "include_events"}
# navigate.include로 같은 응답에 실을 수 있는 추가 조회.
INCLUDES = {"records", "product", "state", "events"}


def view_options(options: dict) -> dict:
    """조작/탐색 매개변수에서 출력 옵션만 골라낸다."""
    return {key: value for key, value in options.items() if key in VIEW_OPTIONS}


def parse_expect(options: dict, default_timeout: int) -> tuple[dict | None, int]:
    """expect 매개변수를 대기 조건으로 검증한다. 없으면 (None, timeout)이다."""
    expect = options.get("expect")
    if expect is None:
        return None, default_timeout
    if not isinstance(expect, dict):
        raise BrowserError("INVALID_ARGUMENT", "expect는 대기 조건 객체입니다.")
    conditions = parse_conditions(expect)
    return conditions, parse_timeout(expect.get("timeout_ms"), default_timeout)


async def verify_expectation(browser, tab, expect: dict | None, timeout_ms: int, baseline: dict, verification: dict) -> None:
    """expect가 있으면 조건을 기다리고 결과를 verification에 기록한다. 실패는 오류다."""
    if expect is None:
        verification["expected_state_reached"] = None
        return
    outcome = await wait_for(browser, tab, expect, timeout_ms, baseline)
    verification["expected_state_reached"] = outcome["satisfied"]
    verification["met"] = outcome["met"]
    verification["unmet"] = outcome["unmet"]
    verification["waited_ms"] = outcome["waited_ms"]
    if not outcome["satisfied"]:
        try:
            title = await asyncio.wait_for(tab.page.title(), 2)
        except Exception:
            title = ""
        raise BrowserError("EXPECTATION_NOT_MET", "조작은 전달되었지만 기대한 페이지 상태에 도달하지 못했습니다.",
                           verification=verification, unmet=outcome["unmet"], met=outcome["met"],
                           page={"url": tab.page.url, "title": title},
                           reason="; ".join(f"{u['condition']}: expected {u['expected']!r}, actual {u['actual']!r}" for u in outcome["unmet"]))


def parse_include(options: dict) -> list[str]:
    """include 매개변수(추가 조회 목록)를 검증한다."""
    include = options.get("include", [])
    if not isinstance(include, list) or any(not isinstance(i, str) for i in include) or set(include) - INCLUDES:
        raise BrowserError("INVALID_ARGUMENT", "include는 records/product/state/events 배열입니다.", allowed=sorted(INCLUDES))
    extract = options.get("extract", {})
    if not isinstance(extract, dict):
        raise BrowserError("INVALID_ARGUMENT", "extract는 extract 명령의 매개변수 객체입니다.")
    return include


async def goto(browser, tab, url: str, settle_ms: int, load_timeout_ms: int = 10000) -> dict:
    """실제 탐색을 수행하고 HTTP 상태를 돌려준다. load는 load_timeout_ms까지만 기다린다.

    광고/추적 스크립트가 많은 페이지는 `load`가 10초 넘게 지연되지만 본문은 이미 완성돼 있다.
    로딩 품질 검증(readiness)이 켜져 있으면 load는 짧게(2초)만 기다리고 내용 판정에 맡긴다.
    """
    try:
        response = await tab.page.goto(url, wait_until="domcontentloaded")
        try:
            await tab.page.wait_for_load_state("load", timeout=min(browser.config.timeout_ms, load_timeout_ms))
        except PlaywrightTimeout:
            pass
        await settle(tab.page, settle_ms)
    except PlaywrightTimeout as error:
        raise BrowserError("PAGE_UNRESPONSIVE", "탐색 제한 시간이 지났습니다.", url=url) from error
    except PlaywrightError as error:
        raise BrowserError("NETWORK_FAILED", str(error), url=url) from error
    return {"http_status": response.status if response else tab.status}


async def navigate(browser, url: str, tab_id: str | None = None, **options) -> dict:
    """실제 Chromium 탐색 후 로딩 품질을 검증하고 기본으로 페이지 요약을 반환한다.

    흐름: goto → load(≤10초) → readiness 판정(제한 시간 내 반복) → 비어 있으면 reload 재시도 →
    `empty`/`blocked`는 오류(ready.on_fail=warn이면 경고) → expect 검증 → 요약/화면 + include 조회.
    """
    url = validate_url(url)
    if not browser.tabs:
        await browser.create_profile()
    tab = browser.get_tab(tab_id)
    mode = options.get("mode", "summary")
    if not isinstance(mode, str):
        raise BrowserError("INVALID_ARGUMENT", "mode는 문자열입니다.")
    expect, timeout_ms = parse_expect(options, browser.config.timeout_ms)
    ready = parse_ready(options, browser.config.timeout_ms)
    include = parse_include(options)
    settle_ms = options.get("settle_ms", 150)
    limits = parse_limits(options, browser.config.max_bytes)
    # minimal 모드는 관측(DOM 수집)을 아예 하지 않으므로 탐색 전 기준선도 잡지 않는다. 변경 비교는 다음 관측에서 새로 시작한다.
    if tab.previous is None and mode != "minimal":
        tab.previous = await browser.observe(tab)
    baseline = baseline_of(browser, tab)
    load_timeout = 2000 if ready is not None else 10000
    outcome = await goto(browser, tab, url, settle_ms, load_timeout)
    readiness = None
    attempts = 1
    if ready is not None:
        readiness = await assess(browser, tab, ready, outcome["http_status"])
        # 빈 껍데기는 새로 고침으로 한 번 더 시도한다. 차단/로그인/HTTP 오류는 재시도해도 같다.
        while readiness["status"] in {"empty", "loading"} and attempts <= ready["retry"]:
            attempts += 1
            browser.emit("navigation_retry", tab=tab.id, url=url, attempt=attempts, reason=readiness["status"])
            outcome = await goto(browser, tab, url, settle_ms, load_timeout)
            readiness = await assess(browser, tab, ready, outcome["http_status"])
        readiness["attempts"] = attempts
        if readiness["status"] in HINTS:
            readiness["hint"] = HINTS[readiness["status"]]
    content_ready = None if readiness is None else readiness["status"] == "ready"
    verification = {"action_dispatched": True, "navigation": True, "http_status": outcome["http_status"],
                    "content_ready": content_ready, "readiness": readiness["status"] if readiness else None}
    if readiness and readiness["status"] in {"empty", "blocked", "error"} and ready["on_fail"] == "error":
        code = {"empty": "PAGE_EMPTY", "blocked": "PAGE_BLOCKED", "error": "NETWORK_FAILED"}[readiness["status"]]
        try:
            title = await asyncio.wait_for(tab.page.title(), 2)
        except Exception:
            title = ""
        raise BrowserError(code, {"empty": "페이지가 로드됐지만 실제 내용이 없습니다(빈 껍데기).",
                                  "blocked": "사이트가 자동화 접근을 차단했습니다.",
                                  "error": "Chromium 오류 페이지가 표시됐습니다."}[readiness["status"]],
                           url=url, page={"url": tab.page.url, "title": title, "http_status": outcome["http_status"]},
                           readiness=readiness, verification=verification, hint=readiness.get("hint"),
                           recovery="응답은 오류지만 탭에는 현재 문서가 남아 있습니다. snapshot/query로 내용을 확인할 수 있습니다.")
    await verify_expectation(browser, tab, expect, timeout_ms, baseline, verification)
    if mode == "minimal":
        try:
            title = await asyncio.wait_for(tab.page.title(), 2)
        except Exception:
            title = ""
        page = {"url": tab.page.url, "title": title, "http_status": outcome["http_status"]}
        result = minimal_view(page, readiness, verification, browser.activity_view(baseline["sequence"], tab.id))
        # 요약 관측을 생략했으므로 다음 관측(action/snapshot)이 새 기준선을 잡게 한다.
        tab.previous = None
        tab.event_cursor = browser.sequence
    else:
        view = view_options(options)
        if mode == "summary" and readiness:
            view["kind"] = readiness.get("kind")
        result = await browser.snapshot(tab.id, mode=mode, **view)
        result["verification"] = verification
        if readiness:
            result["readiness"] = readiness
    # include: 같은 응답에 레코드/상품/상태를 실어 왕복을 줄인다. 각 조회는 자체 예산을 가진다.
    if "records" in include:
        result["records"] = await browser.extract(tab.id, **options.get("extract", {}))
    if "product" in include:
        result["product"] = await browser.product(tab.id)
    if "state" in include:
        result["state"] = await browser.page_state(tab.id)
    if "events" in include and "events" not in result:
        result["events"] = browser.event_view(baseline["sequence"])["events"]
    runtime = browser.announce_runtime()
    if runtime:
        result["runtime"] = runtime
    if mode == "minimal":
        result["budget"] = budget_report(result, limits, 0, False)
    else:
        # include/readiness/runtime를 더한 뒤의 실제 크기로 갱신해 budget.bytes가 응답 전체를 뜻하게 한다(v0.3.0).
        result["budget"].update(measure(result))
    return result


async def act(browser, action: str, tab_id: str | None = None, **options) -> dict:
    """조작 직전 전체 상태를 확보하고 전달/수신/기대 상태를 검증한 뒤 변경분 또는 요청한 화면을 반환한다."""
    if action not in ELEMENT_ACTIONS | PAGE_ACTIONS:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 조작입니다.", action=action)
    tab = browser.get_tab(tab_id)
    settle_ms = options.get("settle_ms", 150)
    if type(settle_ms) is not int or not 0 <= settle_ms <= 5000:
        raise BrowserError("INVALID_ARGUMENT", "settle_ms는 0~5000 정수입니다.")
    verify = options.get("verify", True)
    if not isinstance(verify, bool):
        raise BrowserError("INVALID_ARGUMENT", "verify는 true/false입니다.")
    expect, timeout_ms = parse_expect(options, browser.config.timeout_ms)
    element = None
    probe_frame = tab.page.main_frame
    if action in ELEMENT_ACTIONS:
        if not isinstance(options.get("element"), str):
            raise BrowserError("INVALID_ARGUMENT", "이 조작에는 element ID가 필요합니다.")
        element = await browser.element_handle(tab, options["element"])
        # 이벤트 기록은 요소가 속한 프레임 창에 남으므로 같은 프레임에서 읽어야 한다.
        probe_frame = tab.elements.get(tab.ids.short(tab.ids.resolve(options["element"]))) or probe_frame
    if tab.previous is None:
        tab.previous = await browser.observe(tab)
    baseline = baseline_of(browser, tab)
    token = uuid.uuid4().hex[:12]
    names = PROBE_EVENTS.get(action, [])
    probe_installed = False
    if verify and names:
        try:
            if element is not None:
                probe_installed = bool(await element.evaluate(PROBE_INSTALL, [token, names]))
            else:
                probe_installed = bool(await probe_frame.evaluate(PROBE_INSTALL_DOCUMENT, [token, names]))
        except PlaywrightError:
            probe_installed = False
    try:
        await perform(browser, tab, element, action, options)
        await settle(tab.page, settle_ms)
    except PlaywrightTimeout as error:
        raise BrowserError("ELEMENT_NOT_ACTIONABLE", "제한 시간 내에 요소를 조작하지 못했습니다.",
                           element=options.get("element"), reason=str(error)) from error
    except PlaywrightError as error:
        code = "STALE_ELEMENT" if "detached" in str(error) or "destroyed" in str(error) else "ACTION_FAILED"
        raise BrowserError(code, str(error), element=options.get("element")) from error
    finally:
        if element:
            await element.dispose()
    verification = await inspect(browser, tab, action, token, probe_frame if probe_installed else None, baseline, verify)
    await verify_expectation(browser, tab, expect, timeout_ms, baseline, verification)
    result = await browser.snapshot(tab.id, mode=options.get("mode", "changes"), **view_options(options))
    result["verification"] = verification
    result.setdefault("delta", {})["url"] = {"before": baseline["url"], "after": tab.page.url} if tab.page.url != baseline["url"] else None
    return result


async def inspect(browser, tab, action: str, token: str, probe_frame, baseline: dict, verify: bool) -> dict:
    """조작 뒤 이벤트 수신 여부와 탐색 발생 여부를 읽어 검증 블록을 만든다.

    probe_frame은 탐지기를 설치한 프레임이며 None이면 설치에 실패한 것이다.
    """
    navigated = tab.page.url != baseline["url"] or any(
        e["seq"] > baseline["sequence"] and e.get("tab") == tab.id and e["type"] in {"navigation", "loading_started"}
        for e in browser.events)
    verification = {"action_dispatched": True, "event_received": None, "events": [], "navigation": navigated}
    if not verify:
        verification["event_received"] = None
        verification["reason"] = "verify=false"
        return verification
    if action in NAVIGATION_ACTIONS:
        verification["event_received"] = navigated
        verification["reason"] = None if navigated else "탐색 이벤트가 관측되지 않았습니다(방문 기록 끝 또는 동일 문서)."
        return verification
    if probe_frame is None:
        verification["reason"] = "이벤트 탐지기를 설치하지 못했습니다(활동 추적기 없음)."
        return verification
    try:
        received = await asyncio.wait_for(probe_frame.evaluate(PROBE_READ, token), 2)
    except (PlaywrightError, asyncio.TimeoutError):
        received = None
    if received is None:
        verification["event_received"] = None
        verification["reason"] = "문서가 교체되어 이벤트 기록을 읽을 수 없습니다." if navigated else "이벤트 기록을 읽지 못했습니다."
    else:
        verification["events"] = received
        verification["event_received"] = bool(received)
        if not received:
            verification["reason"] = "페이지가 입력 이벤트를 받지 못했습니다. 가림/비활성화/리스너 없음을 의심하세요."
    return verification


async def perform(browser, tab, element, action: str, options: dict) -> None:
    """각 조작은 Playwright의 가시성/활성화 검사를 거치며 force 입력을 기본 제공하지 않는다."""
    page = tab.page
    if action in {"click", "open_link"}:
        await element.click()
    elif action == "double_click":
        await element.dblclick()
    elif action == "hover":
        await element.hover()
    elif action in {"fill", "clear", "type"}:
        value = "" if action == "clear" else options.get("text")
        if not isinstance(value, str):
            raise BrowserError("INVALID_ARGUMENT", "text 문자열이 필요합니다.")
        if action == "type":
            await element.type(value)
        else:
            await element.fill(value)
    elif action in {"check", "uncheck"}:
        desired = action == "check"
        native = await element.evaluate("el => el.matches('input[type=checkbox],input[type=radio]')")
        if native:
            await element.set_checked(desired)
        else:
            role = await element.get_attribute("role")
            if role not in {"checkbox", "radio", "switch"}:
                raise BrowserError("ELEMENT_NOT_ACTIONABLE", "체크할 수 있는 요소가 아닙니다.")
            if (await element.get_attribute("aria-checked") == "true") != desired:
                await element.click()
            if (await element.get_attribute("aria-checked") == "true") != desired:
                raise BrowserError("ACTION_FAILED", "체크 상태 변경을 확인하지 못했습니다.")
    elif action == "select":
        values = options.get("values")
        if not isinstance(values, (str, list)) or isinstance(values, list) and not all(isinstance(v, str) for v in values):
            raise BrowserError("INVALID_ARGUMENT", "values는 문자열 또는 문자열 배열입니다.")
        await element.select_option(value=values)
    elif action == "upload":
        files = options.get("files")
        if not isinstance(files, list) or not all(isinstance(path, str) for path in files):
            raise BrowserError("INVALID_ARGUMENT", "files는 로컬 파일 경로 배열입니다.")
        for path in files:
            if not Path(path).is_file():
                raise BrowserError("FILE_NOT_FOUND", "업로드 파일을 찾지 못했습니다.", path=path)
        await element.set_input_files(files)
    elif action == "submit":
        submitted = await element.evaluate("""el => {
          const form = el instanceof HTMLFormElement ? el : el.form || el.closest('form');
          if (!form) return false;
          form.requestSubmit();
          return true;
        }""")
        if not submitted:
            raise BrowserError("ELEMENT_NOT_ACTIONABLE", "연결된 폼이 없습니다.")
    elif action == "close_modal":
        closed = await element.evaluate("""el => {
          if (el instanceof HTMLDialogElement) { el.close(); return true; }
          return false;
        }""")
        if not closed:
            await element.press("Escape")
    elif action == "scroll_into_view":
        await element.scroll_into_view_if_needed()
    elif action in {"back", "forward", "reload"}:
        method = {"back": page.go_back, "forward": page.go_forward, "reload": page.reload}[action]
        await method(wait_until="domcontentloaded")
    elif action == "press":
        key = options.get("key")
        if not isinstance(key, str) or not key:
            raise BrowserError("INVALID_ARGUMENT", "key 문자열이 필요합니다.")
        if options.get("element"):
            target = await browser.element_handle(tab, options["element"])
            try:
                await target.press(key)
            finally:
                await target.dispose()
        else:
            await page.keyboard.press(key)
    elif action == "scroll":
        dx, dy = options.get("x", 0), options.get("y", 600)
        if not all(isinstance(v, (int, float)) and abs(v) <= 100000 for v in (dx, dy)):
            raise BrowserError("INVALID_ARGUMENT", "스크롤 값이 범위를 벗어났습니다.")
        if options.get("element"):
            target = await browser.element_handle(tab, options["element"])
            try:
                await target.evaluate("(el, delta) => el.scrollBy(delta.x, delta.y)", {"x": dx, "y": dy})
            finally:
                await target.dispose()
        else:
            await page.mouse.wheel(dx, dy)
    elif action == "coordinate_click":
        x, y = options.get("x"), options.get("y")
        if not all(isinstance(v, (int, float)) and 0 <= v <= 100000 for v in (x, y)):
            raise BrowserError("INVALID_ARGUMENT", "좌표는 0 이상의 유한한 수여야 합니다.")
        await page.mouse.click(x, y)
    elif action == "drag_drop":
        target = await browser.element_handle(tab, options.get("target", ""))
        try:
            await element.scroll_into_view_if_needed()
            await target.scroll_into_view_if_needed()
            source_box, target_box = await element.bounding_box(), await target.bounding_box()
            if not source_box or not target_box:
                raise BrowserError("ELEMENT_NOT_ACTIONABLE", "드래그 원본/대상이 보이지 않습니다.")
            await page.mouse.move(source_box["x"] + source_box["width"] / 2, source_box["y"] + source_box["height"] / 2)
            await page.mouse.down()
            try:
                await page.mouse.move(target_box["x"] + target_box["width"] / 2, target_box["y"] + target_box["height"] / 2, steps=12)
                await page.mouse.move(target_box["x"] + target_box["width"] / 2, target_box["y"] + target_box["height"] / 2)
            finally:
                await page.mouse.up()
        finally:
            await target.dispose()
