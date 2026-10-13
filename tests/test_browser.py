"""실제 Chromium을 통해 사용자 요구의 핵심 읽기/쓰기/세션 계약을 검증한다.

v0.1.0 요구사항(P0~P2)별 검증은 아래 `test_p*` 함수에 있다. 기존 v0.0.1 계약은
출력 형식 변경(기본 text, compact 요소)에 맞춰 `format="elements", compact=False`로 읽는다.
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

from aribrowser.actions import act
from aribrowser.browser import Browser
from aribrowser.engine import Config
from aribrowser.errors import BrowserError
from aribrowser.protocol import Protocol

from .conftest import command, find_id

SHORT_ID = re.compile(r"^t\d+e\d+$")
FULL = {"format": "elements", "compact": False}


async def test_semantics_identity_states_and_diff(app):
    """레이블·접근성·상태·안정 ID와 추가/수정/삭제 차이가 함께 유지되는지 검증한다."""
    browser, protocol = app
    state = await command(protocol, "snapshot", include_hidden=True, **FULL)
    name = next(e for e in state["elements"] if e["name"] == "고객 이름" and e["role"] == "textbox")
    assert name["accessibility_source"] == "chromium-ax"
    assert name["description"] == "고객의 성명을 입력하세요"
    assert name["state"]["invalid"] and name["state"]["required"]
    assert name["groups"]["form"]
    assert next(e for e in state["elements"] if e["role"] == "main")["name"] == ""
    assert "secret123" not in json.dumps(state)
    assert any(e["role"] == "button" and e["name"] == "그림자 버튼" for e in state["elements"])
    assert len([e for e in state["elements"] if e["name"] == "프레임 버튼"]) == 2
    hidden = next(e for e in state["elements"] if e["name"] == "숨긴 버튼")
    assert not hidden["state"]["visible"]
    assert not next(e for e in state["elements"] if e["name"] == "접힌 버튼")["state"]["visible"]
    assert next(e for e in state["elements"] if e["name"] == "가려진 버튼")["state"]["occluded"]
    assert not next(e for e in state["elements"] if e["name"] == "화면 밖 버튼")["state"]["in_viewport"]
    result = await command(protocol, "action", action="fill", element=name["id"], text="김민수")
    assert "elements" not in result
    assert any(e["id"] == name["id"] and e["changes"]["value"]["after"] == "김민수" for e in result["changes"]["changed"] if "value" in e["changes"])
    assert (await find_id(protocol, "textbox", "고객 이름")) == name["id"]
    save = await find_id(protocol, "button", "저장")
    result = await command(protocol, "action", action="click", element=save)
    assert any(e["id"] == save and e["changes"].get("state", {}).get("after", {}).get("disabled") for e in result["changes"]["changed"])
    assert (await command(protocol, "find", role="alert"))["elements"][0]["name"] == "저장되었습니다."
    await browser.get_tab().page.evaluate("document.querySelector('#name').remove()")
    result = await command(protocol, "snapshot", mode="changes")
    assert name["id"] in [e["id"] for e in result["changes"]["removed"]]
    bad = await protocol.handle({"command": "action", "params": {"action": "fill", "element": name["id"], "text": "wrong"}})
    assert not bad["ok"]


async def test_queries_partial_output_tables_and_context(app):
    """부분 출력이 전체 기준선을 손상시키지 않고 표/영역/생략 재조회가 동작하는지 검사한다."""
    _, protocol = app
    view = await command(protocol, "snapshot", limit=3, format="elements")
    assert len(view["elements"]) == 3 and view["omitted"]["next_offset"] == 3
    next_view = await command(protocol, "snapshot", offset=3, limit=3, format="elements")
    assert view["elements"][0]["id"] != next_view["elements"][0]["id"]
    assert "changes" not in next_view
    assert not (await command(protocol, "snapshot", mode="changes"))["changes"]["removed"]
    main = await find_id(protocol, "main")
    scoped = await command(protocol, "snapshot", mode="region", region=main, format="elements")
    assert not any(e["role"] == "navigation" for e in scoped["elements"])
    viewport = await command(protocol, "snapshot", mode="viewport", format="elements")
    assert not any(e.get("name") == "화면 밖 버튼" for e in viewport["elements"])
    interactive = await command(protocol, "snapshot", mode="interactive", format="elements")
    assert all(e["actions"] for e in interactive["elements"])
    table = await find_id(protocol, "table", "주문표")
    result = await command(protocol, "table", element=table, rows=[1], columns=[0, 2])
    assert [e.get("text") or e["name"] for e in result["rows"][0]["cells"]] == ["#1042", "배송 중"]
    assert result["omitted_rows"] == 2 and result["omitted_cells"] == 1
    detail = await command(protocol, "detail", element=table)
    assert detail["ancestors"] and detail["children"]


async def test_form_keyboard_shadow_frame_and_scroll(app):
    """체크/선택/날짜/입력/키보드와 프레임/가상 목록의 실제 갱신을 검증한다."""
    browser, protocol = app
    checkbox = await find_id(protocol, "checkbox", "동의")
    for action, expected in [("check", True), ("uncheck", False)]:
        await command(protocol, "action", action=action, element=checkbox)
        assert (await command(protocol, "detail", element=checkbox))["element"]["state"]["checked"] is expected
    await command(protocol, "action", action="check", element=await find_id(protocol, "radio", "개인"))
    await command(protocol, "action", action="select", element=await find_id(protocol, "combobox", "상태"), values="paid")
    date = await find_id(protocol, text="날짜", role=["Date", "textbox"])
    await command(protocol, "action", action="fill", element=date, text="2026-10-11")
    shadow = await find_id(protocol, "textbox", "그림자 이름")
    await command(protocol, "action", action="fill", element=shadow, text="shadow")
    frame_inputs = (await command(protocol, "find", role="textbox", text="프레임 입력"))["elements"]
    for item in frame_inputs:
        assert item["frame"]  # iframe 요소에만 frame이 남는다.
        filled = await command(protocol, "action", action="fill", element=item["id"], text="iframe")
        assert filled["verification"]["event_received"] is True
    # macOS의 End는 문서 스크롤 키이므로 네이티브 줄 끝 단축키를 사용한다.
    end_key = "Meta+ArrowRight" if sys.platform == "darwin" else "End"
    await command(protocol, "action", action="press", element=shadow, key=end_key)
    await command(protocol, "action", action="type", element=shadow, text="!")
    assert (await command(protocol, "detail", element=shadow))["element"]["value"] == "shadow!"
    virtual = await find_id(protocol, "list")
    await command(protocol, "action", action="scroll", element=virtual, y=200)
    assert (await command(protocol, "find", role="button", text="가상 행 10"))["total"] == 1
    outside = await find_id(protocol, "button", "화면 밖 버튼")
    await command(protocol, "action", action="scroll_into_view", element=outside)
    assert (await command(protocol, "detail", element=outside))["element"]["state"]["in_viewport"]
    assert await browser.get_tab().page.locator('#status').input_value() == "paid"


async def test_dialog_modal_popup_and_pointer(app):
    """브라우저 대화상자가 교착되지 않고 모달/새 창/포인터 이벤트가 관측되는지 검사한다."""
    browser, protocol = app
    await command(protocol, "dialog.policy", action="accept")
    await command(protocol, "action", action="click", element=await find_id(protocol, "button", "대화상자"))
    events = await command(protocol, "events")
    assert any(e["type"] == "dialog_closed" and e["action"] == "accept" for e in events["events"])
    await command(protocol, "action", action="click", element=await find_id(protocol, "button", "모달 열기"))
    modal = await find_id(protocol, "dialog")
    assert (await command(protocol, "detail", element=modal))["element"]["state"]["visible"]
    await command(protocol, "action", action="close_modal", element=modal)
    assert not (await command(protocol, "detail", element=modal))["element"]["state"]["visible"]
    await command(protocol, "action", action="double_click", element=await find_id(protocol, "button", "두 번 클릭"))
    assert (await command(protocol, "find", text="두 번 클릭 완료", role="button"))["total"] == 1
    await command(protocol, "action", action="hover", element=await find_id(protocol, "button", "호버"))
    assert (await command(protocol, "find", role="alert", text="호버 완료"))["total"] == 1
    await command(protocol, "action", action="drag_drop", element=await find_id(protocol, "group", "드래그 원본"), target=await find_id(protocol, "group", "드롭 대상"))
    assert await browser.get_tab().page.locator('#drop').inner_text() == "드롭 완료"
    original = browser.active
    await command(protocol, "action", action="click", element=await find_id(protocol, "button", "새 창"))
    tabs = (await command(protocol, "tab.list"))["tabs"]
    assert len(tabs) == 2 and browser.active == original
    new_tab = next(t["id"] for t in tabs if t["id"] != original)
    switched = await command(protocol, "tab.switch", tab=new_tab)
    assert switched["page"]["url"].endswith('/other')
    assert "elements" not in switched  # 탭 전환은 요약만 돌려준다.
    await command(protocol, "tab.close", tab=new_tab)
    assert browser.active == original


async def test_upload_download_and_async_wait(app, tmp_path):
    """업로드는 실제 input 파일 목록을 바꾸고 다운로드는 디스크 완료까지 추적한다."""
    browser, protocol = app
    upload = tmp_path / "sample.txt"
    upload.write_text("upload data", encoding="utf-8")
    await command(protocol, "action", action="upload", element=await find_id(protocol, "button", "첨부 파일"), files=[str(upload)])
    assert await browser.get_tab().page.locator('#filename').inner_text() == "sample.txt"
    await command(protocol, "action", action="click", element=await find_id(protocol, "link", "파일 받기"))
    await command(protocol, "wait", download="any")
    downloads = (await command(protocol, "downloads"))["downloads"]
    assert downloads[0]["status"] == "completed"
    assert Path(downloads[0]["path"]).read_bytes() == b"AriBrowser download verification"
    await command(protocol, "action", action="click", element=await find_id(protocol, "button", "비동기"), settle_ms=0)
    result = await command(protocol, "wait", text="비동기 완료")
    assert result["changes"]["changed"] and result["wait"]["met"]
    response = await protocol.handle({"command": "wait", "params": {"text": "never exists", "timeout_ms": 100}})
    assert response["error"]["code"] == "WAIT_TIMEOUT"
    assert response["error"]["details"]["unmet"][0]["condition"] == "text"


async def test_deferred_media_and_selective_render(app, site):
    """기본 스냅샷이 이미지 전송을 하지 않고 명시한 이미지 하나만 복원하는지 검증한다."""
    browser, protocol = app
    resources = (await command(protocol, "resources", status="deferred"))["resources"]
    assert any(r["url"].endswith('/one.png') for r in resources)
    initial = dict(site["hits"])
    image = await find_id(protocol, "image", "제품 사진")
    detail = (await command(protocol, "detail", element=image))["element"]
    assert detail["media"]["rendered"] is False and not detail["media"]["loaded"]
    picture = await command(protocol, "render", element=image, level="preview", base64=True)
    assert Path(picture["path"]).read_bytes().startswith(b'\x89PNG')
    assert max(picture["size"].values()) <= 480 and picture["base64"]
    assert site["hits"]["/one.png"] > initial.get("/one.png", 0)
    assert site["hits"]["/two.png"] == initial.get("/two.png", 0)
    assert (await command(protocol, "detail", element=image))["element"]["media"]["loaded"]
    original = await command(protocol, "render", element=image, level="original")
    assert original["size"] == {"width": 800, "height": 600}
    assert await browser.get_tab().page.locator('video').evaluate("el => !el.autoplay")
    canvas = await find_id(protocol, text="매출 차트")
    assert (await command(protocol, "detail", element=canvas))["element"]["media"]["needs_visual"]
    region = await command(protocol, "render", target="region", clip={"x": 0, "y": 0, "width": 80, "height": 60}, load_media=False)
    assert region["size"] == {"width": 80, "height": 60}
    resource = next(r["id"] for r in resources if r["url"].endswith('/two.png'))
    fetched = await command(protocol, "resource.request", resource=resource)
    assert Path(fetched["path"]).is_file()


async def test_navigation_errors_history_and_stale_id(app, site):
    """HTTP 오류/리디렉션/뒤로·앞으로와 문서 교체 후 ID 무효화를 검증한다."""
    browser, protocol = app
    old = await find_id(protocol, "button", "저장")
    await command(protocol, "navigate", url=site["url"] + '/redirect')
    assert browser.get_tab().page.url.endswith('/other')
    assert any(e["type"] == "redirect" for e in (await command(protocol, "events"))["events"])
    with pytest.raises(BrowserError) as error:
        await act(browser, "click", element=old)
    assert error.value.code == "STALE_ELEMENT"
    back = await command(protocol, "action", action="back")
    assert browser.get_tab().page.url.rstrip('/') == site["url"]
    assert back["verification"]["navigation"] and back["verification"]["event_received"]
    await command(protocol, "action", action="forward")
    for path, expected in [("/missing", 404), ("/error", 500), ("/auth", 401)]:
        result = await command(protocol, "navigate", url=site["url"] + path)
        assert result["page"]["http_status"] == expected
        assert result["errors"]["http_status"] == expected
    failed = await protocol.handle({"command": "navigate", "params": {"url": "http://127.0.0.1:1"}})
    assert not failed["ok"] and failed["error"]["code"] == "NETWORK_FAILED"


async def test_profile_persistence_and_isolation(app, site):
    """쿠키/로컬 저장소가 프로필 사이에 누출되지 않고 재시작 때 복원되는지 검증한다."""
    browser, protocol = app
    await command(protocol, "cookies.set", cookies=[{"name": "login", "value": "token", "url": site["url"], "expires": 2000000000}])
    await browser.get_tab().page.evaluate("localStorage.setItem('account','user-one')")
    assert (await command(protocol, "cookies.list"))["cookies"][0]["value"] == "[REDACTED]"
    await command(protocol, "profile.create", name="second", viewport={"width": 390, "height": 844}, mobile=True)
    new_tab = next(t for t in browser.tabs.values() if t.profile == 'second')
    await command(protocol, "navigate", tab=new_tab.id, url=site["url"])
    assert await new_tab.page.evaluate("localStorage.getItem('account')") is None
    assert not (await command(protocol, "cookies.list", profile="second"))["cookies"]
    closed = await command(protocol, "profile.close", name="default")
    assert closed["processes"]["remaining"] == [] and closed["lock"]["released"]
    await command(protocol, "profile.create", name="default")
    restored = next(t for t in browser.tabs.values() if t.profile == 'default')
    await command(protocol, "navigate", tab=restored.id, url=site["url"])
    assert await restored.page.evaluate("localStorage.getItem('account')") == "user-one"
    assert (await command(protocol, "cookies.list", profile="default", include_values=True))["cookies"][0]["value"] == 'token'


@pytest.mark.parametrize("payload, code", [
    ([], "INVALID_REQUEST"), ({"command": "eval"}, "UNKNOWN_COMMAND"),
    ({"command": "snapshot", "params": {"limit": -1}}, "INVALID_ARGUMENT"),
    ({"command": "snapshot", "params": {"format": "xml"}}, "INVALID_ARGUMENT"),
    ({"command": "snapshot", "params": {"fields": ["nope"]}}, "INVALID_ARGUMENT"),
    ({"command": "snapshot", "params": {"max_bytes": 10}}, "INVALID_ARGUMENT"),
    ({"command": "action", "params": {"action": "click"}}, "INVALID_ARGUMENT"),
    ({"command": "navigate", "params": {"url": "javascript:alert(1)"}}, "INVALID_ARGUMENT"),
    ({"command": "profile.create", "params": {"name": "../escape"}}, "INVALID_ARGUMENT"),
    ({"command": "snapshot", "params": {"typo": 1}}, "INVALID_ARGUMENT"),
    ({"command": "query", "params": {"selector": "div["}}, "INVALID_ARGUMENT"),
    ({"command": "query", "params": {"selector": "div", "attributes": ["bad attr"]}}, "INVALID_ARGUMENT"),
    ({"command": "find", "params": {"text": "(", "match": "regex"}}, "INVALID_ARGUMENT"),
    ({"command": "wait", "params": {"bogus": 1}}, "INVALID_ARGUMENT"),
    ({"command": "action", "params": {"action": "press", "key": "Enter", "expect": {"nothing": True}}}, "INVALID_ARGUMENT"),
])
async def test_invalid_requests_recover(app, payload, code):
    """잘못된 명령은 구조화된 오류가 되며 후속 요청을 계속 처리한다."""
    _, protocol = app
    result = await protocol.handle(payload)
    assert result["error"]["code"] == code, result
    assert (await command(protocol, "capabilities"))["version"] == "0.2.0"


# ---------------------------------------------------------------- v0.1.0 요구사항 검증


async def test_p0_1_navigation_returns_summary_by_default(app, site):
    """P0-1: 최초 접속은 전체 DOM 대신 요약(제목/URL/상태/영역/오류/생략 수/다음 조회 위치)을 돌려준다."""
    _, protocol = app
    summary = await command(protocol, "navigate", url=site["url"] + "/products")
    assert "elements" not in summary and "text" not in summary
    assert summary["page"]["title"] == "상품 검색" and summary["page"]["loading"] is False
    assert summary["counts"]["total"] > 20 and summary["omitted"]["elements"] == summary["counts"]["total"]
    assert any(r["role"] == "main" for r in summary["regions"]) and any(r["role"] == "navigation" for r in summary["regions"])
    assert summary["headings"][0]["text"] == "검색 결과" and summary["headings"][0]["level"] == 1
    # v0.2.0: 페이지 종류(listing)에 맞춰 extract를 먼저 추천한다.
    assert summary["kind"] == "listing" and summary["omitted"]["next"][0]["command"] == "extract"
    assert "events" not in summary and summary["activity"]["events"] > 0
    assert summary["budget"]["bytes"] < 6000 and summary["budget"]["tokenizer"]
    assert summary["verification"]["http_status"] == 200
    # 요약 크기 제한 매개변수와 세부 모드 선택도 가능하다.
    small = await command(protocol, "navigate", url=site["url"] + "/products", max_regions=1, max_headings=0)
    assert len(small["regions"]) == 1 and small["regions_omitted"] >= 1 and small["headings"] == []
    full = await command(protocol, "navigate", url=site["url"] + "/products", mode="full", format="elements", max_elements=5)
    assert len(full["elements"]) == 5 and full["budget"]["truncated"] and full["budget"]["next_offset"] == 5
    changes = await command(protocol, "navigate", url=site["url"] + "/products", mode="changes", max_elements=3)
    assert len(changes["changes"]["added"]) == 3 and changes["changes"]["omitted"]["added"] > 0


async def test_p0_2_snapshot_formats_fields_and_compaction(app):
    """P0-2: 기본 출력은 텍스트이며 elements/both/summary 형식, 필드 선택, 빈 값·기본 상태 생략, 분할 조회를 지원한다."""
    _, protocol = app
    text = await command(protocol, "snapshot")
    assert "text" in text and "elements" not in text and text["text"].startswith("PAGE")
    both = await command(protocol, "snapshot", format="both", limit=20)
    assert "text" in both and len(both["elements"]) == 20
    compact = await command(protocol, "snapshot", format="elements", limit=400)
    button = next(e for e in compact["elements"] if e.get("name") == "저장")
    assert set(button) <= {"id", "role", "parent", "name", "actions", "state", "input_type", "text"}
    assert "bounds" not in button and "state" not in button  # 모두 기본 상태인 요소는 state 자체가 없다.
    hidden = next(e for e in (await command(protocol, "snapshot", format="elements", include_hidden=True, limit=1000))["elements"] if e.get("name") == "숨긴 버튼")
    assert hidden["state"] == {"visible": False, "in_viewport": False, "actionable": False}
    full = await command(protocol, "snapshot", **FULL, limit=20)
    assert "bounds" in full["elements"][0] and "accessibility_source" in full["elements"][0]
    picked = await command(protocol, "snapshot", format="elements", fields=["role", "name", "href"], limit=20)
    assert all(set(e) <= {"id", "role", "name", "href"} for e in picked["elements"])
    assert picked["scope"]["fields"] == ["role", "name", "href"]
    summary = await command(protocol, "snapshot", mode="summary")
    assert summary["counts"]["total"] > 0 and "elements" not in summary
    assert compact["budget"]["bytes"] < full["budget"]["bytes"] * 0.6
    page1 = await command(protocol, "snapshot", format="elements", max_bytes=2000)
    assert page1["budget"]["truncated"] and page1["budget"]["truncated_by"] == "max_bytes" and page1["budget"]["bytes"] < 4000
    page2 = await command(protocol, "snapshot", format="elements", offset=page1["budget"]["next_offset"], max_bytes=2000)
    assert page2["elements"][0]["id"] != page1["elements"][0]["id"]
    tokens = await command(protocol, "snapshot", format="elements", max_tokens=200)
    assert tokens["budget"]["truncated_by"] == "max_tokens" and tokens["budget"]["elements"] < compact["budget"]["elements"]


async def test_p0_3_record_extraction(app, site):
    """P0-3: 반복 레코드를 자동/선택자로 묶어 추출하고 누락 필드와 중복 제거를 표시한다."""
    browser, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/products")
    auto = await command(protocol, "extract")
    assert auto["detection"]["method"] == "auto" and auto["detection"]["found"] and auto["total"] == 7
    first = auto["records"][0]
    assert first["fields"]["title"] == "아크릴 보드 A" and first["fields"]["url"].endswith("/item/1")
    assert first["fields"]["price"] == {"raw": "35,000원", "value": 35000, "currency": "KRW"}
    assert first["fields"]["shipping"] == "3,000원" and first["fields"]["image"].endswith("/one.png")
    assert SHORT_ID.match(first["id"]) and SHORT_ID.match(auto["detection"]["container"])
    # 각 레코드의 필드는 같은 카드에서만 나온다(상품 B 가격이 A에 섞이지 않는다).
    assert [r["fields"]["price"]["value"] for r in auto["records"]] == [35000, 15580, 22900, 99000, 15580, 48000, 6200]
    assert auto["records"][3]["missing"] == {"image": "no_match"}
    deduped = await command(protocol, "extract", dedupe=True)
    assert deduped["matched"] == 6 and deduped["duplicates_removed"] == 1
    explicit = await command(protocol, "extract", container="#products", item="li.prod", limit=2,
                             fields={"name": ".name", "price": {"selector": ".price", "type": "price"},
                                     "link": {"selector": "a", "attr": "href", "type": "url"},
                                     "seller": {"selector": ".seller", "regex": "판매처 (.+)"}, "rating": ".rating"})
    assert explicit["total"] == 7 and len(explicit["records"]) == 2 and explicit["next_offset"] == 2
    assert explicit["records"][0]["fields"] == {"name": "아크릴 보드 A", "price": {"raw": "35,000원", "value": 35000, "currency": "KRW"},
                                                "link": site["url"] + "/item/1", "seller": "가", "rating": None}
    assert explicit["records"][0]["missing"] == {"rating": "no_match"}
    # 레코드 ID를 범위로 하위 링크를 찾아 바로 조작할 수 있다.
    link = await command(protocol, "query", selector="a.name", scope=explicit["records"][0]["id"], fields=["id", "href"])
    assert link["total"] == 1 and link["items"][0]["href"].endswith("/item/1")
    clicked = await command(protocol, "action", action="click", element=link["items"][0]["id"], expect={"url": "/item/1"})
    assert clicked["verification"]["expected_state_reached"] and browser.get_tab().page.url.endswith("/item/1")


async def test_p0_4_action_verification(app, site):
    """P0-4: 전달/수신/기대 상태 도달을 구분하고 기대 미달은 원인과 함께 실패로 보고한다."""
    browser, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/products")
    noop = await find_id(protocol, "button", "정렬 적용")
    plain = await command(protocol, "action", action="click", element=noop)
    verification = plain["verification"]
    assert verification["action_dispatched"] and verification["event_received"] and "click" in verification["events"]
    assert verification["expected_state_reached"] is None and verification["navigation"] is False
    failed = await protocol.handle({"command": "action", "params": {"action": "click", "element": noop, "expect": {"url": "sort=high", "timeout_ms": 300}}})
    assert not failed["ok"] and failed["error"]["code"] == "EXPECTATION_NOT_MET"
    details = failed["error"]["details"]
    assert details["verification"]["action_dispatched"] and details["verification"]["event_received"]
    assert details["unmet"][0]["condition"] == "url" and "sort=high" in details["reason"]
    async_button = await find_id(protocol, "button", "낮은가격순 비동기")
    before = await command(protocol, "page.state")
    ok = await command(protocol, "action", action="click", element=async_button,
                       expect={"url": "sort=low", "network_idle_ms": 100, "text": "낮은가격순 적용", "timeout_ms": 5000})
    assert ok["verification"]["expected_state_reached"] and ok["verification"]["waited_ms"] >= 300
    assert {m["condition"] for m in ok["verification"]["met"]} == {"url", "network_idle_ms", "text"}
    assert ok["delta"]["url"]["after"].endswith("?sort=low&page=1")
    after = await command(protocol, "page.state", compare=before["fingerprint"])
    assert after["preserved"] is False and after["differences"]["sort"] == {"before": "popular", "after": "low"}
    quiet = await command(protocol, "action", action="click", element=noop, verify=False)
    assert quiet["verification"]["event_received"] is None and quiet["verification"]["reason"] == "verify=false"


async def test_p0_5_page_state_detection(app, site):
    """P0-5: 정렬/필터/페이지/전체 건수/페이지당 수/다음 페이지/토글을 구조화해 돌려준다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/products?page=2&sort=high&q=아크릴&brand=x")
    state = await command(protocol, "page.state")
    assert state["sort"]["value"] == "high" and state["sort"]["source"] == "url"
    assert any(c["source"] == "select" and c["label"] == "인기순" for c in state["sort_candidates"])
    assert any(c["source"] == "active_control" and c["label"] == "인기순" for c in state["sort_candidates"])
    pagination = state["pagination"]
    assert pagination["current_page"] == 2 and pagination["total_pages"] == 3 and pagination["has_next"] is True
    assert SHORT_ID.match(pagination["next"]) and [p["page"] for p in pagination["pages"]] == [1, 2, 3]
    assert state["per_page"]["value"] == 60 and state["per_page"]["options"] == [30, 60, 120]
    assert state["total_results"]["value"] == 123 and state["total_results"]["text"] == "전체 (123)"
    assert state["query"]["value"] == "아크릴"
    assert {f["label"] for f in state["filters"]} >= {"배송비 포함", "brand=x"}
    assert any(t["label"] == "배송비 포함" and t["checked"] for t in state["toggles"])
    assert any(t["label"] == "무료배송만" and not t["checked"] for t in state["toggles"])
    assert state["fingerprint"] == {"sort": "high", "filters": sorted(f["label"] for f in state["filters"]), "per_page": 60, "query": "아크릴"}
    assert state["checks"]["sort_detected"] and state["checks"]["pagination_detected"] and state["checks"]["total_results_detected"]
    # 다음 페이지로 이동해도 정렬/필터가 유지되는지 fingerprint 비교로 검증한다.
    await command(protocol, "action", action="click", element=pagination["next"], expect={"url": "page=2"})
    moved = await command(protocol, "page.state", compare=state["fingerprint"])
    assert moved["preserved"] is False and set(moved["differences"]) == {"sort", "filters", "query"}
    plain = await command(protocol, "page.state")
    assert plain["sort"]["source"] == "select" and plain["sort"]["value"] == "popular"
    assert plain["pagination"]["current_page"] == 2 and plain["pagination"]["source"] == "url+navigation"


async def test_p1_1_in_browser_query(app, site):
    """P1-1: CSS 선택자로 브라우저 안에서 선별한 필드만 돌려주며 임의 JS/민감 정보 접근이 없다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/products")
    prices = await command(protocol, "query", selector="#products .price", fields=["text", "price"])
    assert prices["total"] == 6 and prices["items"][1] == {"text": "15,580원", "price": {"raw": "15,580원", "value": 15580, "currency": "KRW"}}
    links = await command(protocol, "query", selector="#products a.name", fields=["id", "text", "href"], attributes=["class"], limit=2)
    assert len(links["items"]) == 2 and links["next_offset"] == 2 and links["items"][0]["@class"] == "name"
    assert SHORT_ID.match(links["items"][0]["id"]) and links["items"][0]["href"].endswith("/item/1")
    card = (await command(protocol, "query", selector="li.prod", fields=["id"], limit=1))["items"][0]["id"]
    scoped = await command(protocol, "query", selector="a", scope=card, fields=["href"])
    assert scoped["total"] == 1 and scoped["items"][0]["href"].endswith("/item/1")
    assert (await command(protocol, "query", selector="a", scope="#products li:nth-child(2)", fields=["href"]))["items"][0]["href"].endswith("/item/2")
    filtered = await command(protocol, "query", selector="li.prod", text="아크릴 보드 [AC] ", match="regex", fields=["text"], dedupe=["text"])
    assert filtered["total"] == 2 and all("보드" in i["text"] for i in filtered["items"])
    budget = await command(protocol, "query", selector="li.prod", fields=["text"], max_bytes=300)
    assert budget["budget"]["truncated"] and budget["omitted"] > 0
    capabilities = await command(protocol, "capabilities")
    assert "eval" not in capabilities["commands"] and "query" in capabilities["commands"]
    assert set(capabilities["commands"]["query"]) >= {"selector", "fields", "attributes", "max_bytes"}
    detail = await command(protocol, "detail", element=links["items"][0]["id"])
    assert detail["element"]["role"] == "link" and detail["element"]["internal_id"].startswith("t")


async def test_p1_2_and_p1_3_find_optimization_and_context(app, site):
    """P1-2/P1-3: 필드 선택, 정확/정규식/AND-OR 검색, 정렬, 중복 제거, 개수 제한, 부모/자식 문맥을 지원한다."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/products")
    exact = await command(protocol, "find", text="아크릴 보드 A", match="exact", role="link")
    assert exact["total"] == 1 and exact["elements"][0]["name"] == "아크릴 보드 A"
    regex = await command(protocol, "find", text="보드 [AB]$", match="regex", role="link", fields=["name", "href"])
    assert regex["total"] == 3 and set(regex["elements"][0]) == {"id", "name", "href"}
    both = await command(protocol, "find", text=["아크릴", "판매처 가"], operator="and", role="listitem")
    assert both["total"] == 1
    either = await command(protocol, "find", text=["보드 A", "보드 C"], operator="or", role="link", sort_by="name")
    assert [e["name"] for e in either["elements"]] == ["아크릴 보드 A", "아크릴 보드 C"]
    deduped = await command(protocol, "find", text="아크릴 보드 B", role="link", dedupe=True)
    assert deduped["total"] == 1 and deduped["duplicates_removed"] == 1
    limited = await command(protocol, "find", text="아크릴", role="listitem", limit=2, fields=["name"])
    assert len(limited["elements"]) == 2 and limited["total"] == 7 and limited["omitted"] == 5 and limited["next_offset"] == 2
    assert limited["budget"]["elements"] == 2 and limited["budget"]["bytes"] < 800
    context = await command(protocol, "find", text="아크릴 보드 A", match="exact", role="link", with_parent=2, with_children=1)
    card = context["elements"][0]
    assert [a["role"] for a in card["ancestors"]] == ["listitem", "list"]
    assert card["children"] == []
    items = await command(protocol, "find", text="아크릴 보드 C", role="listitem", with_children=2, child_roles=["link", "text"], fields=["name"])
    children = items["elements"][0]["children"]
    assert {c["role"] for c in children} == {"link", "text"} and any(c.get("href", "").endswith("/item/3") for c in children)
    assert all(c["depth"] == 1 for c in children) and not any("image" == c["role"] for c in children)
    tree = await command(protocol, "find", text="아크릴 보드 C", role="listitem", with_children=1, fields=["name"])
    assert all("children" not in c for c in tree["elements"][0]["children"])


async def test_p1_4_spa_wait_conditions(app, site):
    """P1-4: 네트워크 안정/요소·텍스트·선택자/URL 변경/값/DOM 안정화 대기와 시간 초과 시 미충족 조건 보고."""
    _, protocol = app
    await command(protocol, "navigate", url=site["url"] + "/products")
    state = await command(protocol, "snapshot", mode="summary")
    assert state["page"]["stability"]["network_inflight"] == 0 and state["page"]["stability"]["dom_quiet_ms"] is not None
    button = await find_id(protocol, "button", "낮은가격순 비동기")
    await command(protocol, "action", action="click", element=button, settle_ms=0, verify=False)
    waited = await command(protocol, "wait", url_changed=True, selector="#sort", value="low", network_idle_ms=50,
                           dom_stable_ms=50, text="낮은가격순 적용", timeout_ms=5000)
    assert {m["condition"] for m in waited["wait"]["met"]} == {"url_changed", "selector", "value", "network_idle_ms", "dom_stable_ms", "text"}
    assert waited["wait"]["waited_ms"] >= 200 and "changes" in waited
    timeout = await protocol.handle({"command": "wait", "params": {"selector": "#products", "state": "detached", "title": "상품", "timeout_ms": 150}})
    assert timeout["error"]["code"] == "WAIT_TIMEOUT"
    assert [u["condition"] for u in timeout["error"]["details"]["unmet"]] == ["selector"]
    assert [m["condition"] for m in timeout["error"]["details"]["met"]] == ["title"]
    bad = await protocol.handle({"command": "wait", "params": {"selector": "div[", "timeout_ms": 100}})
    assert bad["error"]["code"] == "INVALID_ARGUMENT"


async def test_p1_5_short_element_ids(app):
    """P1-5: 짧은 ID를 제공하고 세션 내에서 안정적이며 탭/프레임 사이에 충돌하지 않고 긴 ID도 호환된다."""
    browser, protocol = app
    elements = (await command(protocol, "snapshot", format="elements", limit=1000, include_hidden=True))["elements"]
    ids = [e["id"] for e in elements]
    assert all(SHORT_ID.match(i) for i in ids) and len(set(ids)) == len(ids)
    assert all(len(i) <= 8 for i in ids)
    frame_buttons = [e for e in elements if e.get("name") == "프레임 버튼"]
    assert len(frame_buttons) == 2 and frame_buttons[0]["id"] != frame_buttons[1]["id"] and frame_buttons[0]["frame"] != frame_buttons[1]["frame"]
    save = await find_id(protocol, "button", "저장")
    again = await find_id(protocol, "button", "저장")
    assert save == again
    detail = await command(protocol, "detail", element=save)
    internal = detail["element"]["internal_id"]
    assert len(internal) > 40 and internal.startswith("t1-f")
    assert "internal_id" not in next(e for e in elements if e["id"] == save)
    via_long = await command(protocol, "detail", element=internal)
    assert via_long["element"]["id"] == save
    clicked = await command(protocol, "action", action="hover", element=internal)
    assert clicked["verification"]["action_dispatched"]
    await command(protocol, "tab.new")
    tab2 = (await command(protocol, "tab.list"))["tabs"][-1]["id"]
    assert tab2 == "t2"
    await command(protocol, "navigate", tab=tab2, url=browser.get_tab("t1").page.url)
    other = (await command(protocol, "snapshot", tab=tab2, format="elements", limit=5))["elements"]
    assert all(e["id"].startswith("t2e") for e in other) and not set(e["id"] for e in other) & set(ids)
    assert (await command(protocol, "capabilities"))["element_id_format"].startswith("short")


async def test_p1_6_budget_reporting(app):
    """P1-6: 바이트/요소/토큰 상한을 지정하고 실제 크기·추정 토큰·tokenizer·잘림·다음 위치를 보고한다."""
    _, protocol = app
    for params in ({"format": "elements"}, {"format": "text"}, {"mode": "summary"}, {"mode": "changes"}):
        budget = (await command(protocol, "snapshot", **params))["budget"]
        assert {"bytes", "elements", "estimated_tokens", "tokenizer", "limits", "truncated"} <= set(budget)
        assert budget["limits"]["max_bytes"] == 500_000 and budget["bytes"] > 0
    found = await command(protocol, "find", text="버튼", max_elements=2)
    assert found["budget"]["truncated_by"] == "max_elements" and found["budget"]["next_offset"] == 2 and found["budget"]["elements"] == 2
    unlimited = await command(protocol, "snapshot", format="elements", max_bytes=0, include_hidden=True, limit=10000)
    assert unlimited["budget"]["limits"]["max_bytes"] is None and not unlimited["budget"]["truncated"]
    measured = await command(protocol, "snapshot", format="text", max_bytes=1500)
    assert measured["budget"]["truncated"] and len(measured["text"].encode()) <= 1500 + 400
    assert measured["omitted"]["next_offset"] == measured["budget"]["next_offset"]


async def test_p2_2_close_reports_processes_and_locks(tmp_path, site):
    """P2-2: close는 종료/잔여 프로세스 수와 잠금 해제를 보고하고 잔여 잠금은 다음 실행에서 복구한다."""
    profile_dir = tmp_path / "profiles" / "default"
    profile_dir.mkdir(parents=True)
    if sys.platform != "win32":
        os.symlink("ghost-host-999999", profile_dir / "SingletonLock")
    browser = Browser(Config(data_dir=tmp_path, no_sandbox=True, timeout_ms=3000))
    protocol = Protocol(browser)
    await command(protocol, "navigate", url=site["url"])
    events = (await command(protocol, "events"))["events"]
    if sys.platform != "win32":
        assert any(e["type"] == "profile_lock_recovered" for e in events)
    doctor = await command(protocol, "doctor", launch=False)
    assert doctor["session"]["own_processes"] >= 1 and doctor["session"]["open_profiles"] == ["default"]
    cleanup = await command(protocol, "session.cleanup")
    assert cleanup["processes"]["requested"] == 0  # 현재 세션의 프로세스는 정리 대상이 아니다.
    report = await command(protocol, "close")
    assert report["closed"] and report["processes"]["terminated"] >= 1 and report["processes"]["remaining"] == 0
    assert report["locks"]["released"] and report["locks"]["remaining"] == []
    assert not (profile_dir / "SingletonLock").exists() and not (profile_dir / "SingletonLock").is_symlink()
    assert (await protocol.handle({"command": "capabilities"}))["error"]["code"] == "SESSION_CLOSED"


async def test_p2_3_doctor_diagnostics(app):
    """P2-3: 버전/Chromium 경로/사용자/DISPLAY/AppArmor/userns/샌드박스 검사와 단계별 코드를 돌려준다."""
    browser, protocol = app
    report = await command(protocol, "doctor")
    assert report["aribrowser"] == "0.2.0" and report["chromium"]["exists"] and report["chromium"]["version"]
    assert {"name", "uid", "is_root"} <= set(report["user"]) and "DISPLAY" in report["display"]
    assert {c["id"] for c in report["checks"]} >= {"user", "display", "chromium", "sandbox_launch"}
    assert report["sandbox"]["ok"] and report["sandbox"]["no_sandbox_flag"] is True
    assert report["summary"]["ok"] is True and report["summary"]["error_code"] is None
    if sys.platform.startswith("linux"):
        assert report["apparmor"]["applicable"] and "restrict_unprivileged_userns" in report["apparmor"]
        assert report["userns"]["unprivileged_allowed"] in {True, False, None}
    capabilities = await command(protocol, "capabilities")
    assert "SANDBOX_ROOT_USER" in capabilities["diagnostic_codes"] and capabilities["sandbox"] is False
