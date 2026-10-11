"""요소 ID를 실제 Chromium 입력으로 바꾸고 조작 전후의 의미 차이를 반환한다."""

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from .errors import BrowserError

ELEMENT_ACTIONS = {"click", "open_link", "double_click", "hover", "fill", "type", "clear", "check",
                   "uncheck", "select", "upload", "submit", "scroll_into_view", "close_modal", "drag_drop"}
PAGE_ACTIONS = {"back", "forward", "reload", "press", "scroll", "coordinate_click"}


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


async def navigate(browser, url: str, tab_id: str | None = None, **options) -> dict:
    """실제 Chromium 탐색 후 URL/HTTP 상태와 변경분을 반환한다."""
    url = validate_url(url)
    if not browser.tabs:
        await browser.create_profile()
    tab = browser.get_tab(tab_id)
    if tab.previous is None:
        tab.previous = await browser.observe(tab)
    try:
        await tab.page.goto(url, wait_until="domcontentloaded")
        await settle(tab.page, options.get("settle_ms", 150))
    except PlaywrightTimeout as error:
        raise BrowserError("PAGE_UNRESPONSIVE", "탐색 제한 시간이 지났습니다.", url=url) from error
    except PlaywrightError as error:
        raise BrowserError("NETWORK_FAILED", str(error), url=url) from error
    return await browser.snapshot(tab.id, mode=options.get("mode", "changes"))


async def act(browser, action: str, tab_id: str | None = None, **options) -> dict:
    """조작 직전 전체 상태를 확보하고 성공 후 변경분 또는 요청한 화면을 반환한다."""
    if action not in ELEMENT_ACTIONS | PAGE_ACTIONS:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 조작입니다.", action=action)
    tab = browser.get_tab(tab_id)
    settle_ms = options.get("settle_ms", 150)
    if type(settle_ms) is not int or not 0 <= settle_ms <= 5000:
        raise BrowserError("INVALID_ARGUMENT", "settle_ms는 0~5000 정수입니다.")
    element = None
    if action in ELEMENT_ACTIONS:
        if not isinstance(options.get("element"), str):
            raise BrowserError("INVALID_ARGUMENT", "이 조작에는 element ID가 필요합니다.")
        element = await browser.element_handle(tab, options["element"])
    if tab.previous is None:
        tab.previous = await browser.observe(tab)
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
    return await browser.snapshot(tab.id, mode=options.get("mode", "changes"))


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
