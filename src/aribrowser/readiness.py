"""탐색 결과가 "요청한 내용이 실제로 로드된 페이지"인지 판정한다(로딩 품질 검증).

v0.1.0까지 `navigate`는 `load` 이벤트와 HTTP 상태만으로 성공을 보고했다. 그래서
AliExpress 상세 페이지처럼 HTTP 200으로 빈 골격만 내려오거나 보안 문자(captcha)
프레임만 있는 경우에도 성공으로 보고되어 Agent가 빈 결과를 다시 확인하고 재시도하는
부담을 졌다. v0.2.0의 `navigate`는 readiness.js가 모은 신호를 이 모듈로 분류해
다음 상태 중 하나를 돌려주고, `empty`/`blocked`는 기본으로 오류 응답을 만든다.

- `ready`: 본문 텍스트가 충분하고 차단·로딩 표시가 없다.
- `loading`: 스켈레톤/스피너가 보이거나 SPA 루트가 비어 있다(제한 시간 안에서 다시 검사).
- `empty`: 제한 시간이 지나도 본문 텍스트가 `min_text`에 못 미친다(빈 껍데기).
- `blocked`: 보안 문자/차단 페이지 근거가 있거나 HTTP 403/429다.
- `login_required`: 비밀번호 입력이 보이고 그 밖의 내용이 거의 없다.
- `http_error`: HTTP 4xx/5xx 문서(404 등)는 렌더된 문서이므로 오류 응답은 아니다.
- `error`: Chromium 오류 페이지(chrome-error://)다.

판정은 휴리스틱이므로 응답의 `readiness.signals`에 근거를 그대로 싣고, `ready` 옵션으로
기준(`min_text`, `selector`, `text`, `timeout_ms`, `retry`, `on_fail`)을 바꿀 수 있다.
"""

import asyncio
import re
import time

from playwright.async_api import Error as PlaywrightError

from .errors import BrowserError

READY_KEYS = {"min_text", "selector", "text", "timeout_ms", "retry", "on_fail"}
STATUSES = {"ready", "loading", "empty", "blocked", "login_required", "http_error", "error"}
# 차단 페이지의 제목/본문 문구. 본문이 짧을 때만 적용해 긴 정상 문서의 우연한 일치를 피한다.
BLOCKED_TEXT = re.compile(
    r"access denied|forbidden|just a moment|verify you are human|are you a robot|robot check|unusual traffic|"
    r"automated (?:access|queries|requests)|too many requests|rate limit|security check|please verify|"
    r"bot detection|attention required|checking your browser|unauthorized (?:ai |automated )?agent|"
    r"비정상적인 접근|자동 입력 방지|보안 문자|자동화된 요청|접근이 차단|비정상 접근|일시적으로 차단|"
    r"사람인지 확인|로봇이 아닙니다|과도한 요청", re.IGNORECASE)
# 로딩 품질 상태별 Agent 안내.
HINTS = {
    "empty": "페이지가 빈 껍데기입니다. 사이트가 JavaScript로 늦게 채우거나 자동화 접근에 빈 응답을 준 경우입니다. "
             "ready.timeout_ms를 늘리거나 ready.selector로 기다릴 요소를 지정하고, 반복되면 같은 프로필로 --headed 실행을 검토하세요.",
    "blocked": "사이트가 자동화 접근을 차단했습니다(보안 문자/차단 페이지). 같은 프로필을 --headed로 열어 사람이 한 번 인증하면 "
               "쿠키가 보존됩니다. 즉시 재시도는 차단을 강화할 수 있습니다.",
    "login_required": "로그인 페이지입니다. 자격 증명이 있으면 폼을 채우고, 없으면 작업을 중단하세요.",
    "http_error": "서버가 오류 상태를 돌려줬습니다. URL을 확인하거나 잠시 후 다시 시도하세요.",
    "error": "Chromium 오류 페이지입니다. 네트워크/DNS/인증서 문제를 확인하세요.",
    "loading": "제한 시간 안에 로딩 표시가 사라지지 않았습니다. ready.timeout_ms를 늘리거나 wait(dom_stable_ms)를 쓰세요.",
}


def parse_ready(options: dict, default_timeout: int) -> dict | None:
    """`ready` 매개변수를 검증한다. false면 None(검사 생략), 생략/true/객체면 정책 사전이다."""
    value = options.get("ready", True)
    if value is False:
        return None
    if value is True or value is None:
        value = {}
    if not isinstance(value, dict):
        raise BrowserError("INVALID_ARGUMENT", "ready는 true/false 또는 {min_text, selector, text, timeout_ms, retry, on_fail} 객체입니다.")
    unknown = set(value) - READY_KEYS
    if unknown:
        raise BrowserError("INVALID_ARGUMENT", "알 수 없는 ready 옵션입니다.", options=sorted(unknown), allowed=sorted(READY_KEYS))
    policy = {"min_text": value.get("min_text", 120), "selector": value.get("selector"), "text": value.get("text"),
              "timeout_ms": value.get("timeout_ms", min(default_timeout, 8000)), "retry": value.get("retry", 1),
              "on_fail": value.get("on_fail", "error")}
    if type(policy["min_text"]) is not int or not 0 <= policy["min_text"] <= 100000:
        raise BrowserError("INVALID_ARGUMENT", "ready.min_text는 0~100000 정수입니다.")
    if type(policy["timeout_ms"]) is not int or not 0 <= policy["timeout_ms"] <= 30000:
        raise BrowserError("INVALID_ARGUMENT", "ready.timeout_ms는 0~30000 정수입니다.")
    if type(policy["retry"]) is not int or not 0 <= policy["retry"] <= 3:
        raise BrowserError("INVALID_ARGUMENT", "ready.retry는 0~3 정수입니다.")
    if policy["on_fail"] not in {"error", "warn"}:
        raise BrowserError("INVALID_ARGUMENT", "ready.on_fail은 error 또는 warn입니다.")
    for key in ("selector", "text"):
        if policy[key] is not None and (not isinstance(policy[key], str) or not policy[key] or len(policy[key]) > 1000):
            raise BrowserError("INVALID_ARGUMENT", f"ready.{key}는 1~1000자 문자열입니다.")
    return policy


# 단수형 경로(item/product/goods/detail/dp)와 상품 번호 매개변수는 상세 페이지, 복수형/검색/목록 경로는 목록 페이지로 본다.
PRODUCT_URL = re.compile(r"/(?:item|product|goods|detail|dp|info|prod|pdp|p)(?:/|\.|\?|$)|[?&](?:pcode|itemno|item_id|product_id|goods_no|goodsno|prodno|pid)=", re.IGNORECASE)
LISTING_URL = re.compile(r"/(?:search|list|category|categories|catalog|collections?|brand|w)(?:/|\.|\?|$)|[?&](?:q|query|keyword|k|search|cat|category)=", re.IGNORECASE)


def page_kind(signals: dict) -> str:
    """신호와 URL 패턴으로 페이지 종류(listing/product/login/article/other)를 추정한다. 요약의 다음 명령 추천에 쓴다."""
    structured = signals.get("structured") or {}
    url = signals.get("url") or ""
    if signals.get("password_inputs", 0) > 0 and signals.get("text_chars", 0) < 3000:
        return "login"
    og_type = (structured.get("og_type") or "").lower()
    if og_type.startswith("product") or (structured.get("json_ld_product") and signals.get("repeated_groups", 0) < 6):
        return "product"
    listing_url, product_url = bool(LISTING_URL.search(url)), bool(PRODUCT_URL.search(url))
    if listing_url and not product_url and signals.get("repeated_groups", 0) >= 2:
        return "listing"
    if product_url and signals.get("prices", 0) >= 1 and signals.get("h1"):
        return "product"
    if signals.get("repeated_groups", 0) >= 3 and signals.get("prices", 0) >= 3:
        return "listing"
    # 반복 그룹이 하나뿐이어도 항목이 많고 가격이 여럿이면 목록이다(Amazon 결과처럼 카드 컨테이너가 하나인 구조, v0.3.0).
    if signals.get("repeated_max", 0) >= 8 and signals.get("prices", 0) >= 3:
        return "listing"
    if signals.get("prices", 0) >= 1 and signals.get("h1") and signals.get("repeated_groups", 0) < 6:
        return "product"
    if signals.get("repeated_groups", 0) >= 5:
        return "listing"
    if og_type == "article" or (signals.get("main_text_chars") or 0) > 2000:
        return "article"
    return "other"


def classify(signals: dict, policy: dict, http_status: int | None, probe: dict | None = None) -> dict:
    """readiness.js 신호와 HTTP 상태로 로딩 상태를 분류한다. `probe`는 selector/text 조건 검사 결과다."""
    reasons = []
    text_chars = signals.get("text_chars", 0)
    content_chars = signals.get("content_text_chars", text_chars)
    # 폼/목록 위주 페이지는 텍스트가 적어도 상호작용 요소가 충분하면 내용이 있는 것이다.
    # 머리글/바닥글의 링크는 제외한 내용 영역 기준(content_*)을 우선한다.
    interactive = signals.get("content_links", signals.get("links", 0)) + signals.get("content_inputs", signals.get("inputs", 0)) \
        + signals.get("content_buttons", signals.get("buttons", 0))
    rich = interactive >= 8 or (signals.get("headings", 0) >= 1 and interactive >= 4)
    # 작은 정적 문서(노드 수십 개, SPA 루트/로딩 표시 없음)는 텍스트가 적어도 "완성된" 페이지다.
    # 빈 껍데기는 스크립트·골격 요소로 DOM이 크거나 SPA 루트가 비어 있다는 점이 다르다.
    small_static = signals.get("nodes", 0) < 60 and not signals.get("app_root") and not signals.get("skeletons") and not signals.get("spinners")
    rich = rich or (small_static and (signals.get("headings", 0) >= 1 or content_chars > 0))
    captcha = signals.get("captcha") or []
    short = text_chars < max(policy["min_text"] * 10, 2000)
    status = "ready"
    if signals.get("error_page"):
        status, reasons = "error", ["chrome_error_page"]
    elif http_status in {403, 429} or (captcha and (short or any(e.get("visible") for e in captcha))):
        status = "blocked"
        if http_status in {403, 429}:
            reasons.append(f"http_{http_status}")
        if captcha:
            reasons.append("captcha_evidence")
    elif short and BLOCKED_TEXT.search((signals.get("title") or "") + " " + (signals.get("sample") or "")):
        status, reasons = "blocked", ["blocked_text"]
    elif http_status is not None and http_status >= 400:
        status, reasons = "http_error", [f"http_{http_status}"]
    elif signals.get("password_inputs", 0) > 0 and text_chars < 1500 and signals.get("inputs", 0) <= 6:
        status, reasons = "login_required", ["password_form"]
    else:
        if probe is not None and not probe.get("satisfied"):
            status = "loading"
            reasons.append("condition_unmet")
        app_root = signals.get("app_root")
        if content_chars < policy["min_text"] and not rich:
            status = "loading"
            reasons.append("text_below_min")
        if app_root and app_root.get("children", 1) == 0 and app_root.get("text_chars", 0) < policy["min_text"] and not rich:
            status = "loading"
            reasons.append("app_root_empty")
        if (signals.get("skeletons", 0) or signals.get("spinners", 0)) and content_chars < policy["min_text"] * 3:
            status = "loading"
            reasons.append("loading_indicators")
        # 제목도 h1도 없는 DOM 큰 문서는 데이터가 채워지지 않은 골격이다(AliExpress 상세의 빈 제목 껍데기).
        if not (signals.get("title") or "").strip() and not signals.get("h1") and signals.get("nodes", 0) >= 60 \
                and content_chars < policy["min_text"] * 5 and not rich:
            status = "loading"
            reasons.append("no_title")
    return {"status": status, "reasons": reasons, "kind": page_kind(signals)}


def finalize(verdict: dict) -> dict:
    """제한 시간이 끝난 뒤 `loading`을 최종 상태로 바꾼다. 조건 미충족만 남았으면 loading, 아니면 empty다."""
    if verdict["status"] == "loading":
        verdict = dict(verdict)
        verdict["status"] = "loading" if verdict["reasons"] == ["condition_unmet"] else "empty"
    return verdict


async def check_probe(tab, policy: dict) -> dict | None:
    """ready.selector/ready.text 조건을 주 프레임에서 검사한다. 조건이 없으면 None이다."""
    if not policy.get("selector") and not policy.get("text"):
        return None
    script = """([selector, text]) => {
      const result = {selector: null, text: null};
      if (selector) {
        try {
          const nodes = [...document.querySelectorAll(selector)];
          result.selector = nodes.some(n => n.checkVisibility ? n.checkVisibility({visibilityProperty: true}) : n.getClientRects().length > 0);
        } catch (error) { result.error = String(error.message); }
      }
      if (text) result.text = (document.body ? document.body.innerText : '').toLowerCase().includes(text.toLowerCase());
      return result;
    }"""
    try:
        result = await asyncio.wait_for(tab.page.main_frame.evaluate(script, [policy.get("selector"), policy.get("text")]), 5)
    except (PlaywrightError, asyncio.TimeoutError):
        return {"satisfied": False, "selector": None, "text": None}
    if result.get("error"):
        raise BrowserError("INVALID_ARGUMENT", "ready.selector를 해석하지 못했습니다.", selector=policy["selector"], reason=result["error"])
    satisfied = all(v for k, v in result.items() if k in {"selector", "text"} and v is not None)
    return {"satisfied": satisfied, "selector": result.get("selector"), "text": result.get("text")}


async def assess(browser, tab, policy: dict, http_status: int | None) -> dict:
    """제한 시간 안에서 신호를 반복 수집해 최종 로딩 상태를 돌려준다.

    `ready`/`blocked`/`login_required`/`http_error`/`error`는 더 기다릴 이유가 없어 즉시
    끝낸다. `loading`은 DOM과 네트워크가 충분히 조용해진 뒤에도 그대로면 더 기다리지 않는다
    (스크립트가 끝났는데도 비어 있는 페이지를 제한 시간 내내 붙들지 않기 위해서다).
    """
    started = time.monotonic()
    deadline = started + policy["timeout_ms"] / 1000
    checks = 0
    signals, verdict = {}, {"status": "loading", "reasons": ["no_signals"], "kind": "other"}
    while True:
        checks += 1
        try:
            signals = await browser.readiness_signals(tab)
        except BrowserError:
            signals = {"text_chars": 0, "captcha": [], "title": "", "sample": "", "unavailable": True}
        probe = await check_probe(tab, policy)
        verdict = classify(signals, policy, http_status, probe)
        now = time.monotonic()
        if verdict["status"] != "loading" or now >= deadline:
            break
        stability = await browser.stability(tab)
        quiet = stability["dom_quiet_ms"] is not None and stability["dom_quiet_ms"] >= 1500 and stability["network_inflight"] == 0 \
            and stability["network_quiet_ms"] >= 1500 and (now - started) >= 1.5
        if quiet and probe is None:
            break
        await asyncio.sleep(0.25)
    verdict = finalize(verdict)
    return {**verdict, "waited_ms": int((time.monotonic() - started) * 1000), "checks": checks,
            "signals": {key: signals.get(key) for key in ("text_chars", "content_text_chars", "nodes", "links", "content_links", "images", "headings",
                                                           "h1", "inputs", "password_inputs", "skeletons", "spinners",
                                                           "app_root", "captcha", "repeated_groups", "repeated_max", "prices", "sample")},
            "probe": probe}
