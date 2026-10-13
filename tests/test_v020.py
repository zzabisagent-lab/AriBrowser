"""v0.2.0 개선 사항 검증: 설치 경로 독립 샌드박스/브라우저 정책, 경량 탐색 응답, 로딩 품질 검증,
한 왕복 조회(include/batch), 붙은 텍스트 해석과 확장 자동 필드, 상품 상세 요약.

실제 Chromium과 로컬 HTTP 사이트(conftest)를 쓴다. 외부 사이트는 호출하지 않는다.
"""

import sys

import pytest

from aribrowser import sandbox
from aribrowser.browser import Browser
from aribrowser.engine import Config
from aribrowser.errors import BrowserError
from aribrowser.protocol import Protocol
from aribrowser.readiness import classify, page_kind

from .conftest import command


# ---------------------------------------------------------------- 샌드박스/브라우저 정책
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="AppArmor 정책과 POSIX 경로 글로브는 Linux 전용")
def test_sandbox_policy_is_install_path_independent():
    """AppArmor 정책은 모든 사용자 캐시와 배포 디렉터리 구조에 붙고, 경로 포함 검사는 글로브를 해석한다."""
    profile = sandbox.render_profile("/opt/custom root/browsers")
    assert "/home/*/.cache/ms-playwright" in profile and "/**/_internal/playwright/driver/package/.local-browsers" in profile
    assert '"' in profile  # 공백이 있는 경로는 따옴표로 감싼다.
    roots = sandbox.profile_roots(profile)
    assert roots[-1] == "/opt/custom root/browsers"
    assert sandbox._pattern_covers("/home/*/.cache/ms-playwright", "/home/alice/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome")
    assert sandbox._pattern_covers("/**/_internal/playwright/driver/package/.local-browsers",
                                   "/srv/agents/x/aribrowser/_internal/playwright/driver/package/.local-browsers/chromium-1300/chrome-linux64/chrome")
    assert not sandbox._pattern_covers("/home/*/.cache/ms-playwright", "/opt/other/chrome")
    # v0.1.0 머리글(aribrowser-root)도 계속 읽는다.
    assert sandbox.profile_roots("# aribrowser-root: /opt/x\nprofile a /opt/x/** {}") == ["/opt/x"]
    assert str(sandbox.SHARED_HELPER) == "/usr/local/lib/aribrowser/chrome-sandbox"
    assert "SANDBOX_HELPER_INVALID" in sandbox.DOCTOR_CODES and "SYSTEM_BROWSER_MISSING" in sandbox.DOCTOR_CODES


def test_launch_error_classification_with_helper_context():
    """같은 샌드박스 오류라도 도우미 손상/정책 없음/root 실행을 구분한다."""
    message = "Chromium sandboxing failed!\nFATAL: No usable sandbox!"
    assert sandbox.classify_launch_error(message, {"is_root": True}) == "SANDBOX_ROOT_USER"
    assert sandbox.classify_launch_error(message, {"helper": {"exists": True, "installed": False}}) == "SANDBOX_HELPER_INVALID"
    assert sandbox.classify_launch_error(message, {"apparmor": {"restrict_unprivileged_userns": 1, "profile_installed": False}}) == "SANDBOX_USERNS_RESTRICTED"
    assert sandbox.classify_launch_error(message, {"userns": {"unprivileged_allowed": False}, "apparmor": {"profile_installed": True, "profile_covers_current": False}}) == "SANDBOX_PROFILE_STALE"


async def test_browser_policy_candidates(tmp_path, monkeypatch, site):
    """auto는 번들 다음에 시스템 브라우저를 두고, system은 설치된 브라우저가 없으면 오류이며, capabilities가 실제 선택을 보고한다."""
    monkeypatch.setattr(sandbox, "system_browsers", lambda: [{"kind": "chrome", "path": "/opt/fake/chrome", "resolved": "/opt/fake/chrome", "snap": False}])
    browser = Browser(Config(data_dir=tmp_path, no_sandbox=True, timeout_ms=3000, browser="auto"))
    await browser.start()
    kinds = [c["kind"] for c in browser.launch_candidates()]
    assert kinds == ["bundled", "chrome"]
    browser.config.browser = "bundled"
    assert [c["kind"] for c in browser.launch_candidates()] == ["bundled"]
    monkeypatch.setattr(sandbox, "system_browsers", lambda: [])
    browser.config.browser = "system"
    with pytest.raises(BrowserError) as error:
        browser.launch_candidates()
    assert error.value.code == "SYSTEM_BROWSER_MISSING"
    browser.config.browser = "auto"
    protocol = Protocol(browser)
    await command(protocol, "navigate", url=site["url"] + "/products")
    capabilities = await command(protocol, "capabilities")
    assert capabilities["browser_policy"] == "auto" and capabilities["browser"]["kind"] == "bundled"
    assert capabilities["browser"]["fallback_from"] is None and "page.product" in capabilities["commands"] and "batch" in capabilities["commands"]
    await browser.close()


async def test_auto_policy_falls_back_to_system_browser(tmp_path, monkeypatch, site):
    """번들 Chromium이 샌드박스 오류로 실패하면 --no-sandbox 없이 시스템 브라우저로 대체하고 그 사실을 이벤트/capabilities로 알린다."""
    browser = Browser(Config(data_dir=tmp_path, no_sandbox=True, timeout_ms=3000, browser="auto"))
    await browser.start()
    bundled = browser.playwright.chromium.executable_path
    # 시스템 브라우저 자리에 번들 실행 파일을 두고, 번들(executable_path=None) 시도만 샌드박스 오류로 실패시킨다.
    monkeypatch.setattr(sandbox, "system_browsers", lambda: [{"kind": "chrome", "path": bundled, "resolved": bundled, "snap": False}])
    real_launch = browser.playwright.chromium.launch_persistent_context

    async def flaky_launch(*args, **kwargs):
        if kwargs.get("executable_path") is None:
            from playwright.async_api import Error

            raise Error("Chromium sandboxing failed!\n[FATAL] No usable sandbox!")
        return await real_launch(*args, **kwargs)

    monkeypatch.setattr(browser.playwright.chromium, "launch_persistent_context", flaky_launch)
    protocol = Protocol(browser)
    result = await command(protocol, "navigate", url=site["url"] + "/products")
    assert result["readiness"]["status"] == "ready"
    info = (await command(protocol, "capabilities"))["browser"]
    assert info["kind"] == "chrome" and info["executable"] == bundled and info["fallback_from"].startswith("SANDBOX_")
    events = (await command(protocol, "events"))["events"]
    assert any(e["type"] == "browser_fallback" and e["reason"].startswith("SANDBOX_") for e in events)
    assert any(e["type"] == "browser_selected" and e["browser"] == "chrome" for e in events)
    await browser.close()
    # bundled 정책은 대체하지 않고 샌드박스 오류를 그대로 돌려준다.
    strict = Browser(Config(data_dir=tmp_path / "strict", no_sandbox=True, timeout_ms=3000, browser="bundled"))
    await strict.start()
    monkeypatch.setattr(strict.playwright.chromium, "launch_persistent_context", flaky_launch)
    with pytest.raises(BrowserError) as error:
        await strict.create_profile()
    assert error.value.code.startswith("SANDBOX_") and error.value.details["tried"][0]["kind"] == "bundled"
    await strict.close()


# ---------------------------------------------------------------- 경량 탐색 응답
async def test_navigation_response_aggregates_events(app, site):
    """탐색/조작 응답은 이벤트 원문 대신 종류별 집계(activity)를 싣고, 원문은 include_events 또는 events로 본다."""
    _, protocol = app
    summary = await command(protocol, "navigate", url=site["url"] + "/products")
    assert "events" not in summary and summary["activity"]["events"] >= 2
    assert summary["activity"]["by_type"].get("navigation", 0) >= 1 and "cursor" in summary["activity"]
    assert summary["errors"]["events"] == [] and summary["errors"]["event_counts"] == {}
    with_events = await command(protocol, "navigate", url=site["url"] + "/products", include_events=True)
    assert isinstance(with_events["events"], list) and any(e["type"] == "navigation" for e in with_events["events"])
    # 오류성 이벤트는 URL을 호스트+경로로 축약해 요약에 몇 건만 싣는다.
    missing = await command(protocol, "navigate", url=site["url"] + "/missing?utm_source=very-long-tracking-parameter-" + "x" * 300)
    assert missing["readiness"]["status"] == "http_error" and missing["verification"]["content_ready"] is False
    assert missing["errors"]["event_counts"] == {"not_found": 1} and len(missing["errors"]["events"][0]["url"]) <= 101
    assert "utm_source" not in missing["errors"]["events"][0]["url"]
    raw = await command(protocol, "events", since=missing["activity"]["since"])
    assert any(e["type"] == "not_found" and "utm_source" in e["url"] for e in raw["events"])
    changes = await command(protocol, "action", action="reload")
    assert "events" not in changes and changes["activity"]["events"] >= 1


# ---------------------------------------------------------------- 로딩 품질 검증
async def test_readiness_waits_for_late_content(app, site):
    """SPA 루트가 늦게 채워지는 페이지는 제한 시간 안에서 기다린 뒤 ready로 보고한다."""
    _, protocol = app
    result = await command(protocol, "navigate", url=site["url"] + "/shell")
    readiness = result["readiness"]
    assert readiness["status"] == "ready" and result["verification"]["content_ready"] is True
    assert readiness["waited_ms"] >= 900 and readiness["checks"] >= 2 and readiness["attempts"] == 1
    assert readiness["signals"]["text_chars"] > 100 and result["headings"][0]["text"] == "늦게 채워진 상품"


async def test_readiness_reports_empty_shell_with_retry(app, site):
    """끝까지 비어 있는 껍데기는 한 번 새로 고친 뒤 PAGE_EMPTY 오류와 근거·안내를 돌려준다."""
    _, protocol = app
    before = site["hits"]["/shell-empty"]
    response = await protocol.handle({"command": "navigate", "params": {"url": site["url"] + "/shell-empty", "ready": {"timeout_ms": 1500}}})
    assert not response["ok"] and response["error"]["code"] == "PAGE_EMPTY"
    details = response["error"]["details"]
    assert details["readiness"]["status"] == "empty" and details["readiness"]["attempts"] == 2
    assert "app_root_empty" in details["readiness"]["reasons"] or "text_below_min" in details["readiness"]["reasons"]
    assert details["verification"]["content_ready"] is False and details["page"]["title"] == "빈 껍데기" and details["hint"]
    assert site["hits"]["/shell-empty"] == before + 2
    events = (await command(protocol, "events"))["events"]
    assert any(e["type"] == "navigation_retry" for e in events)
    # 제목이 비어 있고 머리글/바닥글 링크만 있는 골격(시스템 Chrome에서 본 AliExpress 상세 형태)도 빈 껍데기다.
    chrome = await protocol.handle({"command": "navigate", "params": {"url": site["url"] + "/shell-chrome", "ready": {"timeout_ms": 800, "retry": 0}}})
    assert chrome["error"]["code"] == "PAGE_EMPTY" and "no_title" in chrome["error"]["details"]["readiness"]["reasons"]
    assert chrome["error"]["details"]["readiness"]["signals"]["content_text_chars"] < chrome["error"]["details"]["readiness"]["signals"]["text_chars"]
    # on_fail=warn은 오류 대신 readiness와 content_ready=false를 가진 성공 응답을, ready=false는 검사 생략을 뜻한다.
    warned = await command(protocol, "navigate", url=site["url"] + "/shell-empty", ready={"on_fail": "warn", "retry": 0, "timeout_ms": 500})
    assert warned["readiness"]["status"] == "empty" and warned["verification"]["content_ready"] is False and warned["readiness"]["attempts"] == 1
    skipped = await command(protocol, "navigate", url=site["url"] + "/shell-empty", ready=False)
    assert "readiness" not in skipped and skipped["verification"]["content_ready"] is None
    # ready.selector/text로 기다릴 대상을 지정할 수 있다.
    custom = await command(protocol, "navigate", url=site["url"] + "/shell", ready={"selector": ".price", "text": "늦게", "timeout_ms": 3000})
    assert custom["readiness"]["status"] == "ready" and custom["readiness"]["probe"] == {"satisfied": True, "selector": True, "text": True}
    unmet = await protocol.handle({"command": "navigate", "params": {"url": site["url"] + "/products", "ready": {"selector": "#nope", "timeout_ms": 300}}})
    assert unmet["ok"] and unmet["result"]["readiness"]["status"] == "loading" and unmet["result"]["verification"]["content_ready"] is False


async def test_readiness_detects_blocked_and_login(app, site):
    """보안 문자 프레임/HTTP 403은 PAGE_BLOCKED(재시도 없음), 로그인 폼은 login_required로 구분한다."""
    _, protocol = app
    before = site["hits"]["/blocked"]
    blocked = await protocol.handle({"command": "navigate", "params": {"url": site["url"] + "/blocked"}})
    assert not blocked["ok"] and blocked["error"]["code"] == "PAGE_BLOCKED"
    readiness = blocked["error"]["details"]["readiness"]
    assert readiness["status"] == "blocked" and "captcha_evidence" in readiness["reasons"]
    assert any("_____tmd_____" in e["value"] for e in readiness["signals"]["captcha"]) and readiness["attempts"] == 1
    assert site["hits"]["/blocked"] == before + 1
    forbidden = await protocol.handle({"command": "navigate", "params": {"url": site["url"] + "/forbidden"}})
    assert forbidden["error"]["code"] == "PAGE_BLOCKED" and forbidden["error"]["details"]["readiness"]["reasons"] == ["http_403"]
    assert forbidden["error"]["details"]["page"]["http_status"] == 403
    login = await command(protocol, "navigate", url=site["url"] + "/login")
    assert login["readiness"]["status"] == "login_required" and login["kind"] == "login" and login["verification"]["content_ready"] is False
    assert login["omitted"]["next"][0]["command"] == "find"
    # 작은 정적 문서(제목 하나)는 빈 껍데기가 아니다.
    other = await command(protocol, "navigate", url=site["url"] + "/other")
    assert other["readiness"]["status"] == "ready"


def test_readiness_classification_rules():
    """분류 규칙 단위 검증: 차단 문구는 짧은 문서에서만, 작은 정적 문서는 비어 있지 않음, URL로 페이지 종류 추정."""
    policy = {"min_text": 120, "selector": None, "text": None, "timeout_ms": 1000, "retry": 0, "on_fail": "error"}
    base = {"text_chars": 50, "content_text_chars": 50, "nodes": 20, "links": 0, "inputs": 0, "buttons": 0, "headings": 1,
            "captcha": [], "title": "x", "sample": "x", "password_inputs": 0, "skeletons": 0, "spinners": 0, "app_root": None}
    assert classify(base, policy, 200)["status"] == "ready"
    assert classify({**base, "nodes": 500, "headings": 0}, policy, 200)["status"] == "loading"
    assert classify({**base, "title": "Access Denied"}, policy, 200)["status"] == "blocked"
    # Amazon의 AI Agent 접근 거부 안내 페이지(제목 "Amazon.com", 본문 한 문장 + Continue 버튼)는 차단으로 본다(v0.3.0).
    assert classify({**base, "title": "Amazon.com", "sample": "Continued access by an unauthorized AI agent violates Amazon's Conditions of Use. Continue"}, policy, 200) == {"status": "blocked", "reasons": ["blocked_text"], "kind": "other"}
    assert classify({**base, "text_chars": 50000, "content_text_chars": 50000, "title": "Access Denied"}, policy, 200)["status"] == "ready"
    assert classify({**base, "links": 30, "nodes": 800, "headings": 0}, policy, 200)["status"] == "ready"
    assert classify({**base, "error_page": True}, policy, None)["status"] == "error"
    assert page_kind({"url": "https://shop.example/item/123", "prices": 1, "h1": "상품", "repeated_groups": 10, "structured": {}}) == "product"
    assert page_kind({"url": "https://shop.example/search?q=x", "prices": 0, "repeated_groups": 3, "structured": {}}) == "listing"
    assert page_kind({"url": "https://x/", "structured": {"og_type": "product"}}) == "product"


# ---------------------------------------------------------------- 한 왕복 조회
async def test_navigate_include_and_batch(app, site):
    """navigate.include로 레코드/상품/상태를 같은 응답에 싣고, batch로 여러 명령을 한 왕복에 실행한다."""
    _, protocol = app
    result = await command(protocol, "navigate", url=site["url"] + "/products", include=["records", "state"], extract={"limit": 2, "dedupe": True})
    assert result["records"]["total"] == 7 and len(result["records"]["records"]) == 2 and result["records"]["quality"]["confidence"] == "high"
    assert result["state"]["sort"]["label"] == "인기순" and "budget" in result["records"]
    product = await command(protocol, "navigate", url=site["url"] + "/product/1", include=["product"])
    assert product["kind"] == "product" and product["product"]["detected"] and product["omitted"]["next"][0]["command"] == "page.product"
    bad = await protocol.handle({"command": "navigate", "params": {"url": site["url"], "include": ["nope"]}})
    assert bad["error"]["code"] == "INVALID_ARGUMENT"
    batch = await command(protocol, "batch", steps=[
        {"command": "navigate", "params": {"url": site["url"] + "/products"}},
        {"id": "x", "command": "extract", "params": {"limit": 1, "fields": {"name": ".name"}}},
        {"command": "query", "params": {"selector": "div["}},
        {"command": "page.state", "params": {}},
    ])
    assert batch["total"] == 4 and batch["completed"] == 2 and batch["failed"] == 1 and batch["stopped_at"] == 2
    assert batch["results"][1]["id"] == "x" and batch["results"][1]["result"]["records"][0]["fields"]["name"] == "아크릴 보드 A"
    assert batch["results"][2]["error"]["code"] == "INVALID_ARGUMENT" and len(batch["results"]) == 3
    tolerant = await command(protocol, "batch", stop_on_error=False, steps=[{"command": "query", "params": {"selector": "div["}}, {"command": "page.state", "params": {}}])
    assert tolerant["completed"] == 1 and tolerant["stopped_at"] is None and len(tolerant["results"]) == 2
    nested = await protocol.handle({"command": "batch", "params": {"steps": [{"command": "close"}]}})
    assert nested["error"]["code"] == "INVALID_ARGUMENT"


# ---------------------------------------------------------------- 텍스트 경계와 자동 필드
async def test_extract_text_boundaries_and_auto_fields(app, site):
    """붙어 출력되는 인라인 요소를 올바르게 띄우거나 붙이고(₩12,345 | 1,000+ / US $12.34), 판매량·평점·리뷰·원가·할인을 자동 추출한다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/market")
    result = await command(protocol, "extract")
    assert result["total"] == 4 and result["quality"]["confidence"] == "high" and result["quality"]["coverage"]["price"] == 1
    a, b, c, d = [r["fields"] for r in result["records"]]
    assert a["price"] == {"raw": "₩12,345", "value": 12345, "currency": "KRW"} and a["original_price"]["value"] == 15000
    assert a["discount"] == 18 and a["sold"] == {"raw": "1,000+", "value": 1000, "plus": True}
    assert a["rating"] == 4.5 and a["reviews"]["value"] == 1234 and a["shipping"] == "무료배송"
    assert "₩12,345 ₩15,000" in a["text"] and "1,000+ 판매" in a["text"]
    assert b["price"] == {"raw": "US $12.34", "value": 12.34, "currency": "USD"} and b["sold"]["value"] == 2300
    assert b["rating"] == 4.8 and b["reviews"]["value"] == 56
    assert c["price"]["value"] == 990 and c["rating"] == 4.7 and c["reviews"]["value"] == 4 and "sold" not in c
    assert d["price"] == {"raw": "35,000 원", "value": 35000, "currency": "KRW"} and d["sold"]["value"] == 345
    query = await command(protocol, "query", selector="li.card", fields=["text", "price", "sold", "rating", "reviews", "discount"], limit=1)
    item = query["items"][0]
    assert item["text"].startswith("무선 이어폰 A ₩12,345 ₩15,000 18%↓ 1,000+ 판매") and item["discount"] == {"raw": "18%↓", "percent": 18}
    assert item["sold"]["value"] == 1000 and item["rating"]["value"] == 4.5 and item["reviews"]["value"] == 1234
    spec = await command(protocol, "extract", item="li.card", limit=1,
                         fields={"sold": {"selector": ".sold", "type": "count"}, "stars": {"selector": ".star", "type": "rating"}})
    assert spec["records"][0]["fields"] == {"sold": {"raw": "1,000+ 판매", "value": 1000, "plus": True}, "stars": {"raw": "★4.5 (1,234)", "value": 4.5, "reviews": {"raw": "1,234", "value": 1234, "plus": False}}}
    empty = await command(protocol, "extract", container="footer")
    assert empty["quality"]["confidence"] == "none" and empty["quality"]["hint"]


# ---------------------------------------------------------------- 상품 상세 요약
async def test_page_product_summary(app, site):
    """page.product는 JSON-LD/메타/DOM을 합쳐 상세 페이지를 작게 요약하고 출처를 밝힌다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/product/1")
    result = await command(protocol, "page.product")
    product, sources = result["product"], result["sources"]
    assert result["detected"] and result["confidence"] == "high" and result["structured_data"] == {"json_ld": True, "microdata": False, "open_graph": True}
    assert product["title"] == "무선 이어폰 A" and sources["title"] == "json-ld"
    assert product["price"] == {"raw": "12345 KRW", "value": 12345, "currency": "KRW"} and sources["price"] == "json-ld"
    assert product["original_price"]["value"] == 15000 and sources["original_price"] == "dom" and product["discount"] == 18
    assert product["sold"]["value"] == 1000 and product["rating"] == 4.5 and product["reviews"]["value"] == 1234
    assert product["shipping"] == "무료배송" and product["availability"] == "in_stock" and product["brand"] == "아리" and product["sku"] == "A-1"
    assert product["seller"] == "아리스토어" and product["images"][0].endswith("/one.png")
    assert [o["label"] for o in product["options"]] == ["색상", "용량"] and product["options"][0]["choices"] == 2
    assert product["title_id"].startswith("t1e") and product["price_id"].startswith("t1e")
    assert len(str(result)) < 2500
    await command(protocol, "navigate", url=site["url"] + "/other")
    none = await command(protocol, "page.product")
    assert none["detected"] is False and none["hint"]


@pytest.mark.skipif(sys.platform == "win32", reason="setuid 도우미는 Linux 전용")
def test_shared_helper_status_shape():
    """공용 도우미 상태는 설치 여부/경로/존재 여부를 항상 포함한다."""
    status = sandbox.shared_helper_status()
    assert {"installed", "path"} <= set(status)
    assert sandbox.launch_env(None) == {} or "CHROME_DEVEL_SANDBOX" in sandbox.launch_env(None)
