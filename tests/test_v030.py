"""v0.3.0 개선 사항 검증(Amazon Agent 벤치마크 후속 요구사항): 프로필 로캘/시간대, 한국어 평점·최근 구매량 해석,
null 키에 안전한 중복 제거와 품질 경고, 레코드 자체/관계 기반/canonical URL 필드, explain 모드, minimal 탐색, 실행 엔진 정보.

실제 Chromium과 로컬 HTTP 사이트(conftest)를 쓴다. 외부 사이트는 호출하지 않는다.
"""

import json

import pytest

from aribrowser import sandbox
from aribrowser.browser import Browser
from aribrowser.engine import Config, validate_locale, validate_timezone
from aribrowser.errors import BrowserError
from aribrowser.protocol import Protocol

from .conftest import command


# ---------------------------------------------------------------- P0-1 프로필 로캘/시간대
def test_locale_and_timezone_validation():
    """BCP 47 로캘은 정규화하고, IANA 시간대는 문법과 tz 데이터베이스로 검증한다. 잘못된 값은 INVALID_ARGUMENT다."""
    assert validate_locale("en-US") == "en-US" and validate_locale("ko_kr") == "ko-KR" and validate_locale("zh-hant-tw") == "zh-Hant-TW"
    assert validate_locale(None) is None and validate_timezone(None) is None
    assert validate_timezone("UTC") == "UTC" and validate_timezone("Asia/Seoul") == "Asia/Seoul"
    for bad in ("english", "en-", "e", "en US", 5):
        with pytest.raises(BrowserError) as error:
            validate_locale(bad)
        assert error.value.code == "INVALID_ARGUMENT"
    for bad in ("Seoul", "asia/seoul", "Mars/Olympus", "UTC+9", ""):
        with pytest.raises(BrowserError) as error:
            validate_timezone(bad)
        assert error.value.code == "INVALID_ARGUMENT"


async def test_profile_locale_timezone_applied_and_persisted(tmp_path, site):
    """en-US/ko-KR 프로필이 각각의 Accept-Language·navigator.language·시간대를 쓰고, 다시 열면 설정이 유지된다."""
    browser = Browser(Config(data_dir=tmp_path, no_sandbox=True, timeout_ms=3000))
    protocol = Protocol(browser)
    created = await command(protocol, "profile.create", name="bench", locale="en-US", timezone_id="UTC")
    assert created["settings"]["locale"] == "en-US" and created["settings"]["timezone_id"] == "UTC"
    assert created["runtime"]["runtime_id"].startswith("rt") and created["runtime"]["version"]
    page = await command(protocol, "navigate", url=site["url"] + "/echo", mode="full", format="text")
    text = page["text"]
    assert "accept-language: en-US" in text and "navigator.language: en-US" in text and "timezone: UTC" in text and "offset: 12" in text
    korean = await command(protocol, "profile.create", name="kr", locale="ko-KR", timezone_id="Asia/Seoul")
    assert korean["locale"] == "ko-KR" and "runtime_id" in korean["runtime"] and "version" not in korean["runtime"]
    tab = next(t["id"] for t in korean["tabs"] if t["profile"] == "kr")
    page = await command(protocol, "navigate", url=site["url"] + "/echo", tab=tab, mode="full", format="text")
    assert "accept-language: ko-KR" in page["text"] and "navigator.language: ko-KR" in page["text"]
    assert "timezone: Asia/Seoul" in page["text"] and "offset: 21" in page["text"]
    listed = await command(protocol, "profile.list")
    assert {p["name"]: (p["locale"], p["timezone_id"]) for p in listed["profiles"]} == {"bench": ("en-US", "UTC"), "kr": ("ko-KR", "Asia/Seoul")}
    capabilities = await command(protocol, "capabilities")
    assert capabilities["profiles"]["kr"]["locale"] == "ko-KR" and "locale" in capabilities["profile_settings"]
    # 잘못된 값은 거부한다.
    bad = await protocol.handle({"command": "profile.create", "params": {"name": "bad", "locale": "english"}})
    assert bad["error"]["code"] == "INVALID_ARGUMENT" and "bad" not in browser.contexts
    bad = await protocol.handle({"command": "profile.create", "params": {"name": "bad", "timezone_id": "Mars/Olympus"}})
    assert bad["error"]["code"] == "INVALID_ARGUMENT"
    # 같은 프로필을 설정 없이 다시 열면 저장된 로캘/시간대를 그대로 쓴다.
    await command(protocol, "profile.close", name="kr")
    stored = json.loads((tmp_path / "profiles" / "kr" / "aribrowser-profile.json").read_text(encoding="utf-8"))
    assert stored["locale"] == "ko-KR" and stored["timezone_id"] == "Asia/Seoul"
    reopened = await command(protocol, "profile.create", name="kr")
    assert reopened["settings"]["locale"] == "ko-KR" and reopened["settings"]["timezone_id"] == "Asia/Seoul"
    await browser.close()


# ---------------------------------------------------------------- P0-2 / P1-2 한국어 평점과 최근 구매량
async def test_korean_rating_and_recent_sales(app, site):
    """'별 5개 중 4.3개' 같은 최댓값 우선 평점을 4.3으로 읽고, 기간 한정 구매량은 recent_sales로 분리하며 누적 sold로 오인하지 않는다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/amazon")
    result = await command(protocol, "extract", item=".s-result-item", limit=6)
    fields = [r["fields"] for r in result["records"]]
    assert [f["rating"] for f in fields] == [4.3, 4.1, 4.0, 4.5, 4.2, 4.3]
    assert fields[0]["price"] == {"raw": "₩45,524", "value": 45524, "currency": "KRW"} and "reviews" not in fields[0]  # 맨 숫자 "3,900"은 리뷰 수로 단정하지 않는다
    assert fields[0]["recent_sales"] == {"raw": "지난달 100개 이상 구매", "value": 100, "plus": True, "period": "month"}
    assert fields[1]["recent_sales"] == {"raw": "50+ bought in past month", "value": 50, "plus": True, "period": "month"}
    assert fields[4]["recent_sales"]["value"] == 50 and fields[4]["recent_sales"]["period"] == "month"
    assert all("sold" not in f for f in fields), "기간 한정 구매량을 누적 판매량으로 오인하면 안 된다"
    assert "recent_sales" not in fields[2] and "recent_sales" not in fields[3]
    # 모델명의 2.5는 평점이 아니다(별점 문맥의 4.5만 인정).
    assert fields[3]["rating"] == 4.5
    # 지정 필드(type=rating)와 query 필드도 같은 해석기를 쓴다.
    spec = await command(protocol, "extract", item=".s-result-item", limit=3,
                         fields={"stars": {"selector": ".a-icon-alt", "type": "rating"}, "recent": {"source": "self", "type": "recent_sales"},
                                 "reviews": {"selector": ".rc", "type": "count"}, "sold": {"source": "self", "type": "sold"}})
    assert [r["fields"]["stars"]["value"] for r in spec["records"]] == [4.3, 4.1, 4.0] and spec["records"][0]["fields"]["reviews"]["value"] == 3900
    assert spec["records"][0]["fields"]["recent"] == {"raw": "지난달 100개 이상 구매", "value": 100, "plus": True, "period": "month"}
    assert spec["records"][1]["fields"]["recent"]["value"] == 50 and spec["records"][2]["fields"]["recent"] is None and spec["records"][0]["fields"]["sold"] is None
    # 기간 구매량 문구가 아예 없는 카드는 parse_failed(값 없음), 판매 키워드는 있는데 못 읽으면 unsupported_locale_pattern이다.
    assert spec["records"][2]["missing"]["recent"] == "parse_failed" and spec["records"][0]["missing"]["sold"] == "unsupported_locale_pattern"
    assert spec["records"][0]["fields"]["stars"]["raw"] == "별 5개 중 4.3개"
    query = await command(protocol, "query", selector=".s-result-item", fields=["rating", "recent_sales", "sold"], limit=2)
    assert query["items"][0]["rating"]["value"] == 4.3 and query["items"][0]["recent_sales"]["value"] == 100 and query["items"][0]["sold"] is None
    capabilities = await command(protocol, "capabilities")
    assert "recent_sales" in capabilities["record_auto_fields"] and "recent_sales" in capabilities["query_fields"]


# ---------------------------------------------------------------- P0-3 / P0-4 null 키 중복 제거와 품질
async def test_dedupe_preserves_records_with_empty_keys(app, site):
    """dedupe 키가 전부 null이면 레코드를 보존하고 통계/경고를 돌려준다. 일부만 null이면 유효 키끼리만 중복 제거한다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/amazon")
    # 1) URL 선택자가 틀려 모든 url이 null: 12개 모두 유지(0.2.0은 1개로 줄었다).
    wrong = await command(protocol, "extract", item=".s-result-item", dedupe=["url"], fields={"title": "h2", "url": {"selector": ".nope", "attr": "href", "type": "url"}})
    assert wrong["total"] == 12 and wrong["matched"] == 12 and wrong["duplicates_removed"] == 0
    assert wrong["dedupe"] == {"keys": ["url"], "usable_records": 0, "skipped_records": 12, "duplicates_removed": 0,
                               "key_coverage": {"url": 0.0}, "warning": "dedupe keys were empty; records were preserved"}
    quality = wrong["quality"]
    assert quality["source_records"] == 12 and quality["output_records"] == 12 and quality["duplicate_ratio"] == 0
    assert quality["dedupe_key_coverage"] == {"url": 0.0} and quality["confidence"] != "high"
    assert any("url" in w for w in quality["warnings"]) and quality["missing"] == {"url": {"selector_no_match": 12}}
    assert "self" in quality["hint"] and "closest" in quality["hint"]
    # 2) 일부 URL만 있음(링크 없는 카드 1개): null 레코드는 보존하고 같은 ASIN의 다른 추적 URL은 canonical 기준으로 제거한다.
    partial = await command(protocol, "extract", item=".s-result-item", dedupe=["url"],
                            fields={"title": "h2", "url": {"selector": "h2", "type": "url", "canonical": True}})
    assert partial["total"] == 12 and partial["duplicates_removed"] == 1 and partial["matched"] == 11
    assert partial["dedupe"]["skipped_records"] == 1 and partial["dedupe"]["usable_records"] == 11 and partial["dedupe"]["key_coverage"]["url"] == 0.92
    assert any(r["fields"]["url"] is None for r in partial["records"]) and partial["records"][4]["missing"] == {"url": "attribute_empty"}
    # 3) 복합 키 중 하나만 유효: 유효 조합(title)으로 중복 제거한다.
    composite = await command(protocol, "extract", item=".s-result-item", dedupe=["url", "title"],
                              fields={"title": "h2", "url": {"selector": ".nope", "attr": "href", "type": "url"}})
    assert composite["duplicates_removed"] == 1 and composite["dedupe"]["usable_records"] == 12 and composite["dedupe"]["key_coverage"] == {"url": 0.0, "title": 1.0}
    # 4) 90% 이상 제거: 모든 카드가 같은 브랜드 → 경고와 high가 아닌 신뢰도.
    brand = await command(protocol, "extract", item=".s-result-item", dedupe=["brand"], fields={"brand": ".brand", "title": "h2"})
    assert brand["duplicates_removed"] == 11 and brand["quality"]["duplicate_ratio"] == 0.917 and brand["quality"]["confidence"] == "medium"
    assert any("91.7%" in w for w in brand["quality"]["warnings"])
    # 5) 자동 필드 dedupe=true는 url이 하나라도 있으면 url 키를 쓴다(추적 URL이 다른 같은 상품은 유지).
    auto = await command(protocol, "extract", item=".s-result-item", dedupe=True)
    assert auto["dedupe"]["keys"] == ["url"] and auto["matched"] == 12 and auto["quality"]["dedupe_key_coverage"]["url"] == 0.92
    # 6) query 모드도 같은 규칙을 따른다.
    query = await command(protocol, "query", selector=".s-result-item .brand", fields=["text", "href"], dedupe=["href"])
    assert query["matched"] == 12 and query["dedupe"]["skipped_records"] == 12 and query["dedupe"]["warning"]


# ---------------------------------------------------------------- P1-1 / P1-4 / P1-5 레코드 자체, 관계 탐색, canonical URL
async def test_self_source_closest_link_and_canonical_url(app, site):
    """source=self/:scope는 카드 전체 텍스트, relation=closest와 type=url 자동 탐색은 부모/자식 링크 구조 모두에서 URL을 얻으며 레코드 경계를 넘지 않는다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/amazon")
    result = await command(protocol, "extract", item=".s-result-item", limit=5, fields={
        "text": {"source": "self"}, "scope": {"selector": ":scope"},
        "url": {"selector": "h2", "type": "url", "relation": "closest"},
        "auto": {"selector": "h2", "type": "url", "canonical": True},
        "outside": {"selector": "h2", "type": "url", "relation": "closest", "closest": ".s-main-slot"},
        "asin": {"selector": "h2", "type": "url", "canonical": True, "stable_id": "/dp/([A-Z0-9]{10})"},
        "compact": {"selector": "h2", "type": "url", "canonical": "compact"},
    })
    first, _, _, inner, none = result["records"]
    assert first["fields"]["text"].startswith("AriSound Valeton Rushead Max 기타 헤드폰 앰프 별 5개 중 4.3개 3,900 지난달 100개 이상 구매 ₩45,524")
    assert first["fields"]["scope"] == first["fields"]["text"]
    assert first["fields"]["url"].startswith(site["url"] + "/dp/B07W3K92ZM/ref=sr_1_1?dib=")
    assert first["fields"]["auto"]["canonical"] == site["url"] + "/dp/B07W3K92ZM" and first["fields"]["auto"]["stable_id"] == "B07W3K92ZM"
    assert first["fields"]["auto"]["raw"].startswith(site["url"] + "/dp/B07W3K92ZM/ref=") and set(first["fields"]["auto"]["removed"]) == {"/ref=", "dib", "keywords", "qid", "sr"}
    assert first["sources"] == {"auto": "closest_link", "asin": "closest_link", "compact": "closest_link"} and first["fields"]["outside"] is None and first["missing"]["outside"] == "relation_no_match"
    assert first["fields"]["asin"]["stable_id"] == "B07W3K92ZM"
    assert first["fields"]["compact"] == {"canonical": site["url"] + "/dp/B07W3K92ZM", "stable_id": "B07W3K92ZM"}
    # h2 안에 링크가 있는 구조: closest는 실패하지만 자동 탐색은 내부 링크를 쓴다.
    assert inner["fields"]["url"] is None and inner["missing"]["url"] == "relation_no_match"
    assert inner["fields"]["auto"]["canonical"] == site["url"] + "/dp/B084JB519R" and inner["sources"]["auto"] == "inner_link"
    assert set(inner["fields"]["auto"]["removed"]) == {"th", "psc"}
    # 링크가 없는 카드: 다른 카드의 링크를 빌리지 않는다.
    assert none["fields"]["url"] is None and none["fields"]["auto"] is None and none["missing"]["auto"] == "attribute_empty"
    # 잘못된 명세는 INVALID_ARGUMENT다.
    for bad in ({"x": {"source": "parent"}}, {"x": {"relation": "parent"}}, {"x": {"canonical": True}}, {"x": {"selector": "h2", "type": "url", "relation": "closest", "closest": "a["}}):
        response = await protocol.handle({"command": "extract", "params": {"item": ".s-result-item", "fields": bad}})
        assert response["error"]["code"] == "INVALID_ARGUMENT", bad
    # 상품 상세 요약도 canonical URL과 안정 식별자를 돌려준다.
    await command(protocol, "navigate", url=site["url"] + "/dp/B07W3K92ZM/ref=x?qid=1", ready=False)
    product = await command(protocol, "page.product")
    assert product["canonical_url"] == site["url"] + "/dp/B07W3K92ZM" and product["stable_id"] == "B07W3K92ZM" and product["canonical_source"] == "url"


# ---------------------------------------------------------------- P2-3 explain 모드와 누락 사유
async def test_explain_mode_distinguishes_selector_and_parser_failures(app, site):
    """explain=true는 선택자 일치 수·원문·해석 상태를 필드별로 돌려주고, missing 사유로 선택자 실패와 해석 실패를 구분한다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/market")
    plain = await command(protocol, "extract", item="li.card", limit=1, fields={"stars": {"selector": ".star", "type": "rating"}})
    assert "explain" not in plain["records"][0]
    result = await command(protocol, "extract", item="li.card", limit=4, explain=True, fields={
        "stars": {"selector": ".star", "type": "rating"}, "size": {"selector": ".tit", "type": "rating"},
        "none": ".nope", "rx": {"selector": ".tit", "regex": "없는 (.+)"}, "num": {"selector": ".ship", "type": "number"},
    })
    a, _, c, _ = result["records"]
    assert a["explain"]["stars"] == {"selector_matches": 1, "raw": "★4.5 (1,234)", "parse_status": "ok"}
    assert a["explain"]["none"] == {"selector_matches": 0, "raw": None, "parse_status": "selector_no_match"}
    assert a["explain"]["rx"]["parse_status"] == "regex_no_match" and a["explain"]["rx"]["raw"] == "무선 이어폰 A"
    assert a["explain"]["num"] == {"selector_matches": 1, "raw": "무료배송", "parse_status": "parse_failed"}
    assert c["explain"]["size"] == {"selector_matches": 1, "raw": "아크릴 판 0.5x200x300mm", "parse_status": "parse_failed"}
    assert c["missing"]["size"] == "parse_failed" and c["missing"]["none"] == "selector_no_match"
    assert a["explain"]["size"]["parse_status"] == "parse_failed" and result["records"][1]["explain"]["size"]["parse_status"] == "parse_failed"
    assert result["quality"]["missing"]["none"] == {"selector_no_match": 4} and any("none" in w for w in result["quality"]["warnings"])
    capabilities = await command(protocol, "capabilities")
    assert "unsupported_locale_pattern" in capabilities["missing_reasons"] and "explain" in capabilities["commands"]["extract"]


# ---------------------------------------------------------------- P1-3 minimal 탐색
async def test_minimal_navigate_mode(app, site):
    """mode=minimal은 요약(영역/제목/역할 수/활동)을 빼고 페이지·readiness·verification·include·budget만 돌려주며, 오류 진단은 유지한다."""
    _, protocol = app
    summary = await command(protocol, "navigate", url=site["url"] + "/amazon", include=["records", "state"], extract={"item": ".s-result-item", "limit": 12})
    minimal = await command(protocol, "navigate", url=site["url"] + "/amazon", mode="minimal", include=["records", "state"], extract={"item": ".s-result-item", "limit": 12})
    for key in ("regions", "headings", "counts", "search_box", "activity", "omitted"):
        assert key not in minimal, key
    assert minimal["page"]["url"].endswith("/amazon") and minimal["page"]["title"].startswith("Amazon.com") and minimal["page"]["http_status"] == 200
    assert minimal["readiness"]["status"] == "ready" and minimal["readiness"]["kind"] == "listing" and "signals" not in minimal["readiness"]
    assert minimal["verification"]["content_ready"] is True and minimal["records"]["total"] == 12 and minimal["state"]["checks"]
    assert "errors" not in minimal and minimal["budget"]["bytes"] < summary["budget"]["bytes"]
    overhead_summary = summary["budget"]["bytes"] - summary["records"]["budget"]["bytes"]
    overhead_minimal = minimal["budget"]["bytes"] - minimal["records"]["budget"]["bytes"]
    assert overhead_minimal < overhead_summary * 0.5, (overhead_summary, overhead_minimal)
    # 실패 상태에서는 근거(signals)와 오류를 유지한다.
    missing = await command(protocol, "navigate", url=site["url"] + "/missing", mode="minimal")
    assert missing["readiness"]["status"] == "http_error" and missing["readiness"]["signals"]["text_chars"] >= 0
    assert missing["errors"]["http_status"] == 404 and missing["errors"]["event_counts"] == {"not_found": 1}
    blocked = await protocol.handle({"command": "navigate", "params": {"url": site["url"] + "/blocked", "mode": "minimal"}})
    assert blocked["error"]["code"] == "PAGE_BLOCKED"
    # minimal 탐색 뒤의 조작/변경분 관측은 새 기준선으로 정상 동작한다.
    await command(protocol, "navigate", url=site["url"] + "/products", mode="minimal")
    changed = await command(protocol, "action", action="click", element=(await command(protocol, "query", selector="#async-sort", fields=["id"]))["items"][0]["id"])
    assert changed["verification"]["action_dispatched"] and "changes" in changed
    capabilities = await command(protocol, "capabilities")
    assert "minimal" in capabilities["navigate_modes"]


# ---------------------------------------------------------------- P2-1 실행 엔진 정보
async def test_runtime_info_reported_once_then_by_id(tmp_path, site, monkeypatch):
    """첫 응답은 실제 제품/버전/실행 파일/샌드박스 상태를, 이후 응답은 runtime_id만 싣는다. 대체 실행은 원인을 함께 돌려준다."""
    browser = Browser(Config(data_dir=tmp_path, no_sandbox=True, timeout_ms=3000))
    protocol = Protocol(browser)
    first = await command(protocol, "navigate", url=site["url"] + "/products", mode="minimal")
    runtime = first["runtime"]
    assert runtime["kind"] == "bundled" and runtime["product"].split("/")[1] == runtime["version"] and runtime["version"].count(".") == 3
    assert runtime["executable"] and runtime["resolved_executable"] and runtime["sandbox_active"] is False and runtime["sandbox_source"] == "none"
    assert runtime["fallback_from"] is None and runtime["runtime_id"].startswith("rt")
    second = await command(protocol, "navigate", url=site["url"] + "/products", mode="minimal")
    assert second["runtime"] == {"runtime_id": runtime["runtime_id"]}
    capabilities = await command(protocol, "capabilities")
    assert capabilities["runtime"]["version"] == runtime["version"] and capabilities["browser"]["version"] == runtime["version"]
    await browser.close()
    # 시스템 브라우저 대체: 실행 파일과 대체 원인을 runtime으로 확인한다.
    fallback = Browser(Config(data_dir=tmp_path / "fb", no_sandbox=True, timeout_ms=3000, browser="auto"))
    await fallback.start()
    bundled = fallback.playwright.chromium.executable_path
    monkeypatch.setattr(sandbox, "system_browsers", lambda: [{"kind": "chrome", "path": bundled, "resolved": bundled, "snap": False}])
    real_launch = fallback.playwright.chromium.launch_persistent_context

    async def flaky_launch(*args, **kwargs):
        if kwargs.get("executable_path") is None:
            from playwright.async_api import Error

            raise Error("Chromium sandboxing failed!\n[FATAL] No usable sandbox!")
        return await real_launch(*args, **kwargs)

    monkeypatch.setattr(fallback.playwright.chromium, "launch_persistent_context", flaky_launch)
    created = await command(Protocol(fallback), "profile.create")
    assert created["runtime"]["kind"] == "chrome" and created["runtime"]["fallback_from"].startswith("SANDBOX_")
    assert created["runtime"]["executable"] == bundled and created["runtime"]["version"]
    await fallback.close()


def test_rebase_into_bundle(tmp_path):
    """번들 밖(사용자 캐시)의 Chromium 경로를 번들 디렉터리의 같은 상대 경로(headless면 headless shell)로 바꾼다."""
    cache = tmp_path / "cache"
    local = tmp_path / "bundle" / ".local-browsers"
    for root in (cache, local):
        (root / "chromium-1243" / "chrome-linux64").mkdir(parents=True)
        (root / "chromium-1243" / "chrome-linux64" / "chrome").write_bytes(b"x")
        (root / "chromium_headless_shell-1243" / "chrome-headless-shell-linux64").mkdir(parents=True)
        (root / "chromium_headless_shell-1243" / "chrome-headless-shell-linux64" / "chrome-headless-shell").write_bytes(b"x")
    outside = str(cache / "chromium-1243" / "chrome-linux64" / "chrome")
    assert sandbox.rebase_into_bundle(outside, local, headless=True) == str(local / "chromium_headless_shell-1243" / "chrome-headless-shell-linux64" / "chrome-headless-shell")
    assert sandbox.rebase_into_bundle(outside, local, headless=False) == str(local / "chromium-1243" / "chrome-linux64" / "chrome")
    inside = str(local / "chromium-1243" / "chrome-linux64" / "chrome")
    assert sandbox.rebase_into_bundle(inside, local) is None  # 이미 번들 안이면 그대로
    assert sandbox.rebase_into_bundle(outside, None) is None and sandbox.rebase_into_bundle(None, local) is None
    # 번들에 없는 리비전: headless면 번들의 headless shell로, 아니면 None(원래 선택 유지).
    assert sandbox.rebase_into_bundle(str(cache / "chromium-9999" / "chrome-linux64" / "chrome"), local).endswith("chrome-headless-shell")
    assert sandbox.rebase_into_bundle(str(cache / "chromium-9999" / "chrome-linux64" / "chrome"), local, headless=False) is None
    assert sandbox.bundle_browsers_dir() is None  # 소스 실행은 번들이 아니다
