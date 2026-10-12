"""SPA 비동기 로딩 완료를 판단하는 대기 조건을 한곳에서 정의한다.

`wait` 명령, `action`의 `expect`, `navigate`의 `expect`가 같은 조건 문법을 쓴다.
고정 대기(settle_ms) 대신 다음 조건을 조합할 수 있다.

- `element`/`text`/`selector` + `state`: 요소·텍스트·CSS 선택자의 표시/숨김/연결/활성 상태
- `value`: element 또는 selector가 가리키는 입력의 현재 값(선택 상태 변경 대기)
- `url`, `url_changed`, `title`: 주소/제목 변화
- `network_idle_ms`: 진행 중인 네트워크 요청이 없고 마지막 요청 완료 뒤 일정 시간이 지남
- `dom_stable_ms`: 마지막 DOM 변이 뒤 일정 시간이 지남(화면 안정화)
- `download`: 다운로드 완료

시간 초과 시 충족된 조건(met)과 충족되지 않은 조건(unmet)을 함께 돌려주어
Agent가 어떤 조건이 왜 실패했는지 알 수 있게 한다.
"""

import asyncio
import time

from playwright.async_api import Error as PlaywrightError

from .errors import BrowserError

WAIT_KEYS = {"element", "text", "selector", "state", "url", "url_changed", "title", "network_idle_ms",
             "dom_stable_ms", "value", "download", "timeout_ms"}
STATES = {"visible", "hidden", "attached", "detached", "enabled"}

# 주 프레임에서 선택자 상태를 읽는 고정 코드. 선택자는 데이터로만 전달된다.
SELECTOR_PROBE = """([selector, wantValue]) => {
  let nodes;
  try { nodes = [...document.querySelectorAll(selector)]; } catch (error) { return {error: String(error.message)}; }
  const visible = node => node.checkVisibility ? node.checkVisibility({visibilityProperty: true}) : node.getClientRects().length > 0;
  return {count: nodes.length, visible: nodes.filter(visible).length,
    enabled: nodes.filter(node => !node.disabled && node.getAttribute('aria-disabled') !== 'true').length,
    values: wantValue ? nodes.slice(0, 20).map(node => 'value' in node ? (node.type === 'password' ? '[REDACTED]' : node.value) : (node.textContent || '').trim()) : null};
}"""
DOM_QUIET = "() => { const a = window.__aribrowser_activity__; return a ? performance.now() - a.lastMutation : null; }"


def parse_conditions(params: dict) -> dict:
    """대기 조건을 검증하고 정규화한다. 조건이 하나도 없으면 오류다."""
    unknown = set(params) - WAIT_KEYS
    if unknown:
        raise BrowserError("INVALID_ARGUMENT", "알 수 없는 대기 조건입니다.", conditions=sorted(unknown), allowed=sorted(WAIT_KEYS))
    conditions = {k: v for k, v in params.items() if k != "timeout_ms"}
    state = conditions.get("state", "visible")
    if state not in STATES:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 대기 상태입니다.", state=state, allowed=sorted(STATES))
    for key in ("element", "text", "selector", "url", "title"):
        if key in conditions and (not isinstance(conditions[key], str) or not conditions[key]):
            raise BrowserError("INVALID_ARGUMENT", f"{key}는 비어 있지 않은 문자열입니다.")
    for key in ("network_idle_ms", "dom_stable_ms"):
        if key in conditions and (type(conditions[key]) is not int or not 0 <= conditions[key] <= 30000):
            raise BrowserError("INVALID_ARGUMENT", f"{key}는 0~30000 정수입니다.")
    if "url_changed" in conditions and not isinstance(conditions["url_changed"], bool):
        raise BrowserError("INVALID_ARGUMENT", "url_changed는 true/false입니다.")
    if "value" in conditions and "element" not in conditions and "selector" not in conditions:
        raise BrowserError("INVALID_ARGUMENT", "value 조건에는 element 또는 selector가 필요합니다.")
    if "download" in conditions and not isinstance(conditions["download"], str):
        raise BrowserError("INVALID_ARGUMENT", "download는 다운로드 ID 또는 any입니다.")
    if not any(key in conditions for key in ("element", "text", "selector", "url", "url_changed", "title", "network_idle_ms", "dom_stable_ms", "download")):
        raise BrowserError("INVALID_ARGUMENT", "element/text/selector/url/url_changed/title/network_idle_ms/dom_stable_ms/download 중 하나 이상이 필요합니다.")
    return conditions


def parse_timeout(value, default: int) -> int:
    """timeout_ms 범위를 검증한다."""
    timeout = default if value is None else value
    if type(timeout) is not int or not 1 <= timeout <= 30000:
        raise BrowserError("INVALID_ARGUMENT", "timeout_ms는 1~30000입니다.")
    return timeout


def baseline_of(browser, tab) -> dict:
    """url_changed 같은 상대 조건의 기준이 되는 현재 상태를 기록한다."""
    return {"url": tab.page.url, "sequence": browser.sequence, "time": time.monotonic()}


async def evaluate(browser, tab, conditions: dict, baseline: dict) -> tuple[list[dict], list[dict]]:
    """모든 조건을 한 번 검사해 (충족 목록, 미충족 목록)을 돌려준다. 관측은 필요할 때만 한다."""
    met, unmet = [], []
    state = conditions.get("state", "visible")

    def record(key, satisfied, expected, actual):
        (met if satisfied else unmet).append({"condition": key, "expected": expected, "actual": actual})

    if "url" in conditions:
        record("url", conditions["url"] in tab.page.url, conditions["url"], tab.page.url)
    if conditions.get("url_changed"):
        record("url_changed", tab.page.url != baseline["url"], f"!= {baseline['url']}", tab.page.url)
    if "title" in conditions:
        try:
            title = await asyncio.wait_for(tab.page.title(), 2)
        except (PlaywrightError, asyncio.TimeoutError):
            title = ""
        record("title", conditions["title"].casefold() in title.casefold(), conditions["title"], title)
    if "download" in conditions:
        wanted = conditions["download"]
        done = [d["id"] for d in browser.downloads.values() if d["status"] == "completed" and d["tab"] == tab.id
                and (wanted == "any" or d["id"] == wanted)]
        record("download", bool(done), wanted, done)
    if "network_idle_ms" in conditions:
        quiet = (time.monotonic() - tab.last_network) * 1000
        satisfied = not tab.inflight and quiet >= conditions["network_idle_ms"]
        record("network_idle_ms", satisfied, conditions["network_idle_ms"], {"inflight": len(tab.inflight), "quiet_ms": int(quiet)})
    if "dom_stable_ms" in conditions:
        try:
            quiet = await tab.page.main_frame.evaluate(DOM_QUIET)
        except PlaywrightError:
            quiet = None
        # 활동 추적기가 없는 문서(about:blank 등)는 변이가 없다고 본다.
        satisfied = quiet is None or quiet >= conditions["dom_stable_ms"]
        record("dom_stable_ms", satisfied, conditions["dom_stable_ms"], None if quiet is None else int(quiet))
    if "selector" in conditions:
        try:
            probe = await tab.page.main_frame.evaluate(SELECTOR_PROBE, [conditions["selector"], "value" in conditions])
        except PlaywrightError as error:
            probe = {"error": str(error)}
        if probe.get("error"):
            raise BrowserError("INVALID_ARGUMENT", "selector를 해석하지 못했습니다.", selector=conditions["selector"], reason=probe["error"])
        satisfied = {"attached": probe["count"] > 0, "detached": probe["count"] == 0, "visible": probe["visible"] > 0,
                     "hidden": probe["visible"] == 0, "enabled": probe["enabled"] > 0}[state]
        record("selector", satisfied, {"selector": conditions["selector"], "state": state},
               {"count": probe["count"], "visible": probe["visible"], "enabled": probe["enabled"]})
        if "value" in conditions:
            record("value", conditions["value"] in (probe["values"] or []), conditions["value"], probe["values"])
    if "element" in conditions or "text" in conditions:
        snapshot = await browser.observe(tab)
        needle = conditions.get("text", "").casefold()
        matches = [e for e in snapshot["elements"] if
                   ("element" not in conditions or e["id"] == conditions["element"]) and
                   (not needle or needle in (e["name"] + " " + e["text"] + " " + str(e.get("value") or "")).casefold())]
        satisfied = {"attached": bool(matches), "detached": not matches,
                     "visible": any(e["state"]["visible"] for e in matches),
                     "hidden": not any(e["state"]["visible"] for e in matches),
                     "enabled": any(not e["state"]["disabled"] for e in matches)}[state]
        key = "element" if "element" in conditions else "text"
        record(key, satisfied, {key: conditions[key], "state": state},
               {"matches": len(matches), "visible": sum(1 for e in matches if e["state"]["visible"])})
        if "value" in conditions and "element" in conditions:
            values = [e.get("value") for e in matches]
            record("value", conditions["value"] in values, conditions["value"], values)
    return met, unmet


async def wait_for(browser, tab, conditions: dict, timeout_ms: int, baseline: dict | None = None) -> dict:
    """제한 시간 안에서 조건을 반복 검사한다. 결과는 성공/실패와 조건별 상태를 포함한다."""
    baseline = baseline or baseline_of(browser, tab)
    deadline = time.monotonic() + timeout_ms / 1000
    started = time.monotonic()
    met, unmet = [], []
    while True:
        met, unmet = await evaluate(browser, tab, conditions, baseline)
        if not unmet:
            return {"satisfied": True, "met": met, "unmet": [], "waited_ms": int((time.monotonic() - started) * 1000)}
        if time.monotonic() >= deadline:
            return {"satisfied": False, "met": met, "unmet": unmet, "waited_ms": int((time.monotonic() - started) * 1000)}
        await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
