"""Chromium 수명, 프로필/탭 격리, 네트워크 자원과 비동기 이벤트를 관리한다.

웹사이트는 실제 Chromium에서 실행한다. 쿠키·IndexedDB·로컬 저장소는 프로필
디렉터리에 보존하며 페이지 데이터는 표준입출력 명령과 별도의 신뢰 경계에 둔다.
"""

import asyncio
import re
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from weakref import WeakKeyDictionary

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from .errors import BrowserError


@dataclass
class Config:
    """data_dir는 세션/다운로드 저장소, timeout_ms는 개별 브라우저 작업 상한이다."""

    data_dir: Path = field(default_factory=lambda: Path.home() / ".aribrowser")
    headless: bool = True
    no_sandbox: bool = False
    timeout_ms: int = 10000
    width: int = 1280
    height: int = 800
    mobile: bool = False
    media_policy: str = "defer"
    executable_path: str | None = None


@dataclass
class Tab:
    """탭별 마지막 전체 관측과 이벤트 위치를 보관하여 부분 출력 간섭을 방지한다."""

    id: str
    profile: str
    page: Page
    loading: bool = False
    crashed: bool = False
    status: int | None = None
    previous: dict | None = None
    event_cursor: int = 0
    frames: dict = field(default_factory=dict)
    elements: dict = field(default_factory=dict)
    dialog: object | None = None
    dialog_policy: dict = field(default_factory=lambda: {"action": "dismiss"})


class Engine:
    """여러 영구 프로필과 그 탭을 하나의 순차 Agent 연결 안에서 관리한다."""

    def __init__(self, config: Config | None = None):
        """설정과 유한 크기 관측 버퍼를 준비한다. 생성자에서는 Chromium을 실행하지 않는다."""
        self.config = config or Config()
        self.contexts: dict[str, BrowserContext] = {}
        self.tabs: dict[str, Tab] = {}
        self.active: str | None = None
        self.playwright = None
        self.events = deque(maxlen=2000)
        self.sequence = 0
        self.tab_sequence = 0
        self.resources: OrderedDict[str, dict] = OrderedDict()
        self.resource_sequence = 0
        self.request_ids = WeakKeyDictionary()
        self.allowed: dict[str, set[str]] = {}
        self.policies: dict[str, str] = {}
        self.downloads: dict[str, dict] = {}
        self.tasks: set[asyncio.Task] = set()
        self.closing = False

    def emit(self, kind: str, **data) -> None:
        """시간/증가 번호가 있는 이벤트를 저장한다. 웹 원문은 항상 데이터 필드에만 둔다."""
        self.sequence += 1
        self.events.append({"seq": self.sequence, "time": time.time(), "type": kind, **data})

    def event_view(self, since: int = 0) -> dict:
        """누락 여부와 다음 커서를 포함하여 비동기 이벤트를 재조회 가능하게 제공한다."""
        return {"events": [e for e in self.events if e["seq"] > since], "cursor": self.sequence,
                "events_omitted": bool(self.events and since < self.events[0]["seq"] - 1)}

    def background(self, coroutine) -> None:
        """이벤트 후속 작업을 추적하여 종료 시 다운로드를 기다리고 예외를 관측한다."""
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        """비동기 콜백의 오류가 묻히지 않도록 상태 이벤트로 전환한다."""
        self.tasks.discard(task)
        if not task.cancelled() and task.exception():
            self.emit("background_error", message=str(task.exception()))

    async def start(self) -> None:
        """Playwright 드라이버를 한 번만 시작한다. 브라우저는 프로필 생성 시 지연 실행한다."""
        if not self.playwright:
            self.config.data_dir.mkdir(parents=True, exist_ok=True)
            self.playwright = await async_playwright().start()

    async def create_profile(self, name: str = "default", **options) -> dict:
        """프로필별 Chromium 저장소를 열어 쿠키/로그인을 격리하고 재실행 시 복원한다."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise BrowserError("INVALID_ARGUMENT", "프로필 이름은 영문/숫자/_/- 1~64자입니다.")
        if name in self.contexts:
            raise BrowserError("PROFILE_EXISTS", "이미 열린 프로필입니다.", profile=name)
        await self.start()
        policy = options.get("media_policy", self.config.media_policy)
        if policy not in {"defer", "allow"}:
            raise BrowserError("INVALID_ARGUMENT", "media_policy는 defer 또는 allow입니다.")
        viewport = options.get("viewport", {"width": self.config.width, "height": self.config.height})
        if not isinstance(viewport, dict) or any(type(viewport.get(k)) is not int or not 240 <= viewport[k] <= 7680 for k in ("width", "height")):
            raise BrowserError("INVALID_ARGUMENT", "화면 너비/높이는 240~7680 정수입니다.")
        profile_dir = self.config.data_dir / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.chmod(0o700)
        context = await self.playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=self.config.headless,
            executable_path=self.config.executable_path,
            chromium_sandbox=not self.config.no_sandbox,
            viewport=viewport, is_mobile=options.get("mobile", self.config.mobile),
            has_touch=options.get("mobile", self.config.mobile),
            device_scale_factor=options.get("device_scale_factor", 1),
            accept_downloads=True, service_workers="block" if policy == "defer" else "allow",
            args=["--autoplay-policy=user-gesture-required"],
        )
        self.contexts[name] = context
        self.allowed[name] = set()
        self.policies[name] = policy
        context.set_default_timeout(self.config.timeout_ms)
        await context.add_init_script(script=Path(__file__).with_name("media.js").read_text(encoding="utf-8"))
        await context.route("**/*", lambda route: self._route(name, route))
        context.on("request", lambda request: self._request(name, request))
        context.on("requestfinished", lambda request: self._resource_finish(request, "loaded"))
        context.on("requestfailed", lambda request: self._resource_finish(request, "failed"))
        context.on("response", self._response)
        context.on("page", lambda page: self._attach(name, page))
        context.on("close", lambda: self.emit("profile_closed", profile=name))
        for page in context.pages:
            self._attach(name, page)
        if not context.pages:
            await context.new_page()
        return {"profile": name, "persistent": True, "media_policy": policy, "tabs": await self.list_tabs()}

    def _attach(self, profile: str, page: Page) -> Tab:
        """새 탭/팝업에 동일한 상태 추적기를 설치하며 팝업을 임의로 활성화하지 않는다."""
        for existing in self.tabs.values():
            if existing.page is page:
                return existing
        self.tab_sequence += 1
        tab = Tab(f"t{self.tab_sequence}", profile, page)
        self.tabs[tab.id] = tab
        if self.active is None:
            self.active = tab.id
        self.emit("tab_created", tab=tab.id, profile=profile, url=page.url)
        page.on("close", lambda: self._tab_closed(tab))
        page.on("crash", lambda: self._crash(tab))
        page.on("domcontentloaded", lambda: self.emit("dom_ready", tab=tab.id))
        page.on("load", lambda: self._loaded(tab))
        page.on("framenavigated", lambda frame: self._navigated(tab, frame))
        page.on("request", lambda request: self._loading(tab, request))
        page.on("dialog", lambda dialog: self.background(self._dialog(tab, dialog)))
        page.on("download", lambda download: self.background(self._download(tab, download)))
        page.on("popup", lambda popup: self.emit("popup_created", opener=tab.id,
                                                 tab=self._attach(profile, popup).id))
        page.on("pageerror", lambda error: self.emit("script_error", tab=tab.id, message=str(error)))
        return tab

    def _tab_closed(self, tab: Tab) -> None:
        """닫힌 활성 탭의 후속 탭을 선택하고 폐기된 요소/스냅샷 참조를 해제한다."""
        self.tabs.pop(tab.id, None)
        if self.active == tab.id:
            self.active = next(iter(self.tabs), None)
        self.emit("tab_closed", tab=tab.id)

    def _crash(self, tab: Tab) -> None:
        """엔진 충돌을 일반 네트워크 오류와 구분한다."""
        tab.crashed = True
        self.emit("browser_crashed", tab=tab.id)

    def _loading(self, tab: Tab, request) -> None:
        """주 문서 요청에서만 페이지 로딩 시작을 표시한다."""
        if request.is_navigation_request() and request.frame == tab.page.main_frame:
            tab.loading = True
            tab.status = None
            self.emit("loading_started", tab=tab.id, url=request.url)

    def _loaded(self, tab: Tab) -> None:
        """load 이벤트로 문서 로딩 완료를 알린다. 이후 SPA 비동기 작업은 계속 관측한다."""
        tab.loading = False
        self.emit("loading_finished", tab=tab.id, url=tab.page.url)

    def _navigated(self, tab: Tab, frame) -> None:
        """주 프레임의 URL 변화와 iframe 탐색을 구분해 기록한다."""
        self.emit("navigation" if frame == tab.page.main_frame else "frame_navigation", tab=tab.id, url=frame.url)

    def _request(self, profile: str, request) -> None:
        """이미지/미디어와 실행 자원을 구분하고 자원 이력을 유한 크기로 유지한다."""
        self.resource_sequence += 1
        key = f"r{self.resource_sequence}"
        self.request_ids[request] = key
        tab_id = None
        try:
            tab_id = next((t.id for t in self.tabs.values() if t.page is request.frame.page), None)
        except Exception:
            pass  # 서비스 워커 요청은 Frame이 없을 수 있으며 프로필 수준에서 추적한다.
        self.resources[key] = {"id": key, "url": request.url, "type": request.resource_type,
                               "profile": profile, "tab": tab_id, "status": "loading",
                               "purpose": "display" if request.resource_type in {"image", "media", "font"} else "functional"}
        if len(self.resources) > 2000:
            self.resources.popitem(last=False)

    async def _route(self, profile: str, route) -> None:
        """선택된 URL만 이미지/미디어를 허용하여 디코딩 전에 네트워크 비용을 줄인다."""
        request = route.request
        blocked = self.policies[profile] == "defer" and request.resource_type in {"image", "media"}
        if blocked and request.url not in self.allowed[profile]:
            record = self.resources.get(self.request_ids.get(request))
            if record:
                record["status"] = "deferred"
            await route.abort("blockedbyclient")
        else:
            await route.continue_()

    def _resource_finish(self, request, status: str) -> None:
        """의도적 보류는 네트워크 실패로 덮어쓰지 않고 실제 전송 실패만 보고한다."""
        record = self.resources.get(self.request_ids.get(request))
        if record and record["status"] != "deferred":
            record["status"] = status
            if status == "failed":
                record["error"] = request.failure
                self.emit("network_failed", tab=record["tab"], url=request.url, message=request.failure)
                for tab in self.tabs.values():
                    if tab.id == record["tab"] and request.is_navigation_request():
                        tab.loading = False

    def _response(self, response) -> None:
        """HTTP 상태/리디렉션을 추적하고 404·인증·서버 오류를 별도 분류한다."""
        record = self.resources.get(self.request_ids.get(response.request))
        if record:
            record["http_status"] = response.status
        if response.request.is_navigation_request():
            try:
                frame = response.frame
            except PlaywrightError:
                # 팝업의 첫 탐색 응답은 프레임 객체 생성보다 먼저 도착할 수 있다.
                # 자원 HTTP 상태는 이미 기록했으며 page 이벤트에서 탭을 연결한다.
                return
            tab = next((t for t in self.tabs.values() if t.page is frame.page), None)
            if not tab:
                return
            if frame == tab.page.main_frame:
                tab.status = response.status
            if 300 <= response.status < 400:
                self.emit("redirect", tab=tab.id, url=response.url, location=response.headers.get("location"))
            elif response.status >= 400:
                kind = "not_found" if response.status == 404 else "authentication_required" if response.status in {401, 403} else "server_error" if response.status >= 500 else "http_error"
                self.emit(kind, tab=tab.id, url=response.url, status=response.status)

    async def _dialog(self, tab: Tab, dialog) -> None:
        """기본 dismiss로 명령 교착을 피하고, 사전 설정한 accept/prompt 응답을 적용한다."""
        tab.dialog = dialog
        self.emit("dialog_opened", tab=tab.id, dialog_type=dialog.type, message=dialog.message,
                  default_value=dialog.default_value)
        if tab.dialog_policy["action"] == "accept":
            await dialog.accept(tab.dialog_policy.get("text", ""))
        else:
            await dialog.dismiss()
        tab.dialog = None
        self.emit("dialog_closed", tab=tab.id, action=tab.dialog_policy["action"])

    async def _download(self, tab: Tab, download) -> None:
        """다운로드 이름을 정규화하고 충돌 없는 경로에 저장한 뒤 완료/실패를 보고한다."""
        key = f"d-{uuid.uuid4().hex[:12]}"
        filename = re.sub(r"[^\w.() -]", "_", download.suggested_filename).strip(". ") or "download"
        folder = self.config.data_dir / "downloads" / tab.profile
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{key}-{filename[:160]}"
        record = {"id": key, "tab": tab.id, "url": download.url, "filename": filename,
                  "status": "started", "path": str(destination)}
        self.downloads[key] = record
        self.emit("download_started", **record)
        try:
            await download.save_as(destination)
            failure = await download.failure()
            if failure:
                raise BrowserError("DOWNLOAD_FAILED", failure)
            record.update(status="completed", bytes=destination.stat().st_size)
            self.emit("download_completed", **record)
        except Exception as error:
            record.update(status="failed", error=str(error))
            self.emit("download_failed", **record)

    def get_tab(self, tab_id: str | None = None) -> Tab:
        """명시적 ID 또는 활성 탭을 해석하고 닫힘/충돌 상태를 구분한다."""
        tab = self.tabs.get(tab_id or self.active)
        if not tab:
            raise BrowserError("TAB_NOT_FOUND", "열린 탭을 찾을 수 없습니다.", tab=tab_id)
        if tab.crashed:
            raise BrowserError("BROWSER_CRASHED", "탭의 Chromium 렌더러가 충돌했습니다.", tab=tab.id)
        return tab

    async def list_tabs(self) -> list[dict]:
        """활성 여부, 프로필, URL, 제목과 로딩 상태를 반환한다."""
        result = []
        for tab in list(self.tabs.values()):
            try:
                title = await asyncio.wait_for(tab.page.title(), 2)
            except Exception:
                title = ""
            result.append({"id": tab.id, "profile": tab.profile, "url": tab.page.url,
                           "title": title, "active": self.active == tab.id,
                           "loading": tab.loading, "crashed": tab.crashed})
        return result

    async def new_tab(self, profile: str | None = None) -> Tab:
        """지정 프로필에 탭을 만들고 새 탭을 활성화한다."""
        profile = profile or (self.get_tab().profile if self.tabs else "default")
        if profile not in self.contexts:
            await self.create_profile(profile)
            tab = next(t for t in self.tabs.values() if t.profile == profile)
        else:
            tab = self._attach(profile, await self.contexts[profile].new_page())
        self.active = tab.id
        return tab

    async def close_profile(self, profile: str) -> dict:
        """프로필 브라우저만 닫는다. 사용자 저장소와 다운로드 파일은 보존한다."""
        context = self.contexts.get(profile)
        if not context:
            raise BrowserError("PROFILE_NOT_FOUND", "열린 프로필이 없습니다.", profile=profile)
        await context.close()
        self.contexts.pop(profile, None)
        self.allowed.pop(profile, None)
        self.policies.pop(profile, None)
        return {"closed": profile}

    async def close(self) -> None:
        """진행 중인 다운로드에 짧은 완료 기회를 준 뒤 모든 엔진 자원을 해제한다."""
        self.closing = True
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=5)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        for name in list(self.contexts):
            await self.close_profile(name)
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
