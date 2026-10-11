"""실제 Chromium을 통해 사용자 요구의 핵심 읽기/쓰기/세션 계약을 검증한다."""

import json
import sys
from pathlib import Path

import pytest

from aribrowser.actions import act
from aribrowser.errors import BrowserError

from .conftest import command, find_id


async def test_semantics_identity_states_and_diff(app):
    """레이블·접근성·상태·안정 ID와 추가/수정/삭제 차이가 함께 유지되는지 검증한다."""
    browser, protocol = app
    state = await command(protocol, "snapshot", include_hidden=True)
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
    assert (await command(protocol, "find", role="alert"))["elements"][0]["text"] == "저장되었습니다."
    await browser.get_tab().page.evaluate("document.querySelector('#name').remove()")
    result = await command(protocol, "snapshot", mode="changes")
    assert name["id"] in [e["id"] for e in result["changes"]["removed"]]
    bad = await protocol.handle({"command": "action", "params": {"action": "fill", "element": name["id"], "text": "wrong"}})
    assert not bad["ok"]


async def test_queries_partial_output_tables_and_context(app):
    """부분 출력이 전체 기준선을 손상시키지 않고 표/영역/생략 재조회가 동작하는지 검사한다."""
    _, protocol = app
    view = await command(protocol, "snapshot", limit=3)
    assert len(view["elements"]) == 3 and view["omitted"]["next_offset"] == 3
    next_view = await command(protocol, "snapshot", offset=3, limit=3)
    assert view["elements"][0]["id"] != next_view["elements"][0]["id"]
    assert "changes" not in next_view
    assert not (await command(protocol, "snapshot", mode="changes"))["changes"]["removed"]
    main = await find_id(protocol, "main")
    scoped = await command(protocol, "snapshot", mode="region", region=main)
    assert not any(e["role"] == "navigation" for e in scoped["elements"])
    viewport = await command(protocol, "snapshot", mode="viewport")
    assert not any(e["name"] == "화면 밖 버튼" for e in viewport["elements"])
    interactive = await command(protocol, "snapshot", mode="interactive")
    assert all(e["actions"] for e in interactive["elements"])
    table = await find_id(protocol, "table", "주문표")
    result = await command(protocol, "table", element=table, rows=[1], columns=[0, 2])
    assert [e["text"] for e in result["rows"][0]["cells"]] == ["#1042", "배송 중"]
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
        await command(protocol, "action", action="fill", element=item["id"], text="iframe")
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
    assert result["changes"]["changed"]
    response = await protocol.handle({"command": "wait", "params": {"text": "never exists", "timeout_ms": 100}})
    assert response["error"]["code"] == "WAIT_TIMEOUT"


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
    with pytest.raises(BrowserError):
        await act(browser, "click", element=old)
    await command(protocol, "action", action="back")
    assert browser.get_tab().page.url.rstrip('/') == site["url"]
    await command(protocol, "action", action="forward")
    for path, expected in [("/missing", 404), ("/error", 500), ("/auth", 401)]:
        result = await command(protocol, "navigate", url=site["url"] + path)
        assert result["page"]["http_status"] == expected
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
    await command(protocol, "profile.close", name="default")
    await command(protocol, "profile.create", name="default")
    restored = next(t for t in browser.tabs.values() if t.profile == 'default')
    await command(protocol, "navigate", tab=restored.id, url=site["url"])
    assert await restored.page.evaluate("localStorage.getItem('account')") == "user-one"
    assert (await command(protocol, "cookies.list", profile="default", include_values=True))["cookies"][0]["value"] == 'token'


@pytest.mark.parametrize("payload, code", [
    ([], "INVALID_REQUEST"), ({"command": "eval"}, "UNKNOWN_COMMAND"),
    ({"command": "snapshot", "params": {"limit": -1}}, "INVALID_ARGUMENT"),
    ({"command": "action", "params": {"action": "click"}}, "INVALID_ARGUMENT"),
    ({"command": "navigate", "params": {"url": "javascript:alert(1)"}}, "INVALID_ARGUMENT"),
    ({"command": "profile.create", "params": {"name": "../escape"}}, "INVALID_ARGUMENT"),
    ({"command": "snapshot", "params": {"typo": 1}}, "INVALID_ARGUMENT"),
])
async def test_invalid_requests_recover(app, payload, code):
    """잘못된 명령은 구조화된 오류가 되며 후속 요청을 계속 처리한다."""
    _, protocol = app
    result = await protocol.handle(payload)
    assert result["error"]["code"] == code
    assert (await command(protocol, "capabilities"))["version"] == "0.0.1"
