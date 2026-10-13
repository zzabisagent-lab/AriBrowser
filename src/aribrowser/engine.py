"""Chromium 수명, 프로필/탭 격리, 네트워크 자원과 비동기 이벤트를 관리한다.

웹사이트는 실제 Chromium에서 실행한다. 쿠키·IndexedDB·로컬 저장소는 프로필
디렉터리에 보존하며 페이지 데이터는 표준입출력 명령과 별도의 신뢰 경계에 둔다.

v0.1.0에서 추가된 책임:
- 탭별 진행 중 네트워크 요청 수와 마지막 네트워크 활동 시각을 추적한다(SPA 로딩 감지).
- 프로필 실행 전 잔여 잠금을 진단·복구하고, 실행 실패를 샌드박스/권한/의존성 코드로 구분한다.
- 프로필 종료 후 Chromium 프로세스의 실제 종료와 잠금 해제를 확인해 보고한다.
- 탭마다 짧은 요소 ID 레지스트리를 둔다.

v0.2.0에서 추가된 책임:
- 브라우저 선택(`Config.browser`): `bundled`는 번들 Chromium만, `system`은 설치된 Chrome/Chromium/
  Edge/Brave만, `auto`(기본)는 번들 Chromium이 샌드박스/의존성 문제로 실행되지 않을 때 시스템
  브라우저로 대체한다. 어떤 경우에도 `--no-sandbox`로 대체하지 않으며 대체 사실은
  `browser_fallback` 이벤트와 `browser_info`로 알린다.
- 공용 setuid 샌드박스 도우미(sandbox.launch_env)를 실행 환경 변수로 전달한다.
- 관측 응답에 이벤트 원문 대신 종류별 집계(`activity_view`)를 제공한다.

v0.3.0에서 추가된 책임:
- 프로필 로캘/시간대(`locale`, `timezone_id`)를 검증해 Playwright 영구 컨텍스트에 적용하고, 프로필 디렉터리의
  `aribrowser-profile.json`에 저장해 같은 프로필을 다시 열 때 유지한다(Accept-Language·navigator.language·
  Intl 시간대가 실행 환경이 아니라 프로필 설정을 따르므로 벤치마크 재현성이 좋아진다).
- 실제 실행 엔진 정보(`runtime`): CDP `Browser.getVersion`의 제품/버전, 프로세스 기준 실행 파일(/proc/<pid>/exe),
  대체 사유, 샌드박스 활성 여부와 근거를 첫 `profile.create`/`navigate` 응답과 `capabilities`에 돌려준다.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from weakref import WeakKeyDictionary

from playwright.async_api import BrowserContext, Page, async_playwright
from playwright.async_api import Error as PlaywrightError

from . import processes, sandbox
from .errors import BrowserError
from .ids import IdRegistry


def short_url(url: str, limit: int = 100) -> str:
    """URL을 호스트와 경로 앞부분만 남겨 축약한다. 추적용 쿼리 문자열은 토큰만 낭비한다."""
    try:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}{parts.path}"
    except ValueError:
        base = url
    if len(base) > limit:
        base = base[:limit - 1] + "…"
    return base


@dataclass
class Config:
    """data_dir는 세션/다운로드 저장소, timeout_ms는 개별 브라우저 작업 상한이다.

    max_bytes는 관측 명령 응답의 세션 기본 바이트 상한이다(0이면 무제한). 요청마다
    max_bytes/max_elements/max_tokens로 재지정할 수 있다. no_sandbox는 명시적으로
    요청했을 때만 True가 되며 어떤 실패 경로에서도 자동으로 켜지지 않는다.
    browser는 실행 파일 선택 정책(auto|bundled|system)이며 executable_path가 있으면 그 경로를 쓴다.
    locale/timezone_id는 프로필 생성 시 명시하지 않았을 때 쓰는 세션 기본값이다(None이면 Chromium 기본).
    """

    data_dir: Path = field(default_factory=lambda: Path.home() / ".aribrowser")
    headless: bool = True
    no_sandbox: bool = False
    timeout_ms: int = 10000
    width: int = 1280
    height: int = 800
    mobile: bool = False
    media_policy: str = "defer"
    executable_path: str | None = None
    max_bytes: int = 500_000
    browser: str = "auto"
    locale: str | None = None
    timezone_id: str | None = None


# BCP 47 언어 태그의 실용적 부분집합: 언어(2~3자), 선택적 문자 체계(4자), 지역(2자 또는 3자리 숫자), 변형.
LOCALE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|\d{3}))?(?:-(?:[A-Za-z0-9]{5,8}|\d[A-Za-z0-9]{3}))*$")
# IANA 시간대 ID의 문법: "UTC", "Etc/GMT+9", "Asia/Seoul", "America/Argentina/Buenos_Aires".
TIMEZONE_ID = re.compile(r"^(?:UTC|GMT|Etc/[A-Za-z0-9+\-]+|[A-Z][A-Za-z]+(?:/[A-Za-z0-9_+\-]+){1,2})$")
# 프로필 디렉터리에 보존하는 설정 파일 이름. 같은 프로필을 다시 열 때 로캘/시간대/화면 설정을 유지한다.
PROFILE_SETTINGS = "aribrowser-profile.json"


def validate_locale(value) -> str | None:
    """BCP 47 로캘 태그를 검증하고 정규화한다(`en_US` → `en-US`). None은 "지정 안 함"이다."""
    if value is None:
        return None
    if not isinstance(value, str) or not LOCALE_TAG.match(value.replace("_", "-")):
        raise BrowserError("INVALID_ARGUMENT", "locale은 BCP 47 언어 태그입니다(예: en-US, ko-KR).", locale=value)
    parts = value.replace("_", "-").split("-")
    parts[0] = parts[0].lower()
    for index in range(1, len(parts)):
        if len(parts[index]) == 2:
            parts[index] = parts[index].upper()
        elif len(parts[index]) == 4 and parts[index].isalpha():
            parts[index] = parts[index].title()
    return "-".join(parts)


def validate_timezone(value) -> str | None:
    """IANA 시간대 ID를 검증한다. 시스템에 tz 데이터베이스가 있으면 실제 목록과도 대조한다.

    Windows의 Python에는 tz 데이터가 없을 수 있어(`available_timezones()`가 비어 있음) 그때는 문법 검사만 하고,
    Chromium이 거부하면(`Invalid timezone ID`) 실행 단계에서 INVALID_ARGUMENT로 바꾼다.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not TIMEZONE_ID.match(value):
        raise BrowserError("INVALID_ARGUMENT", "timezone_id는 IANA 시간대 ID입니다(예: UTC, Asia/Seoul).", timezone_id=value)
    try:
        import zoneinfo

        available = zoneinfo.available_timezones()
    except Exception:  # tzdata가 없는 환경
        available = set()
    if available and value not in available:
        raise BrowserError("INVALID_ARGUMENT", "알 수 없는 IANA 시간대 ID입니다.", timezone_id=value)
    return value


@dataclass
class Tab:
    """탭별 마지막 전체 관측과 이벤트 위치를 보관하여 부분 출력 간섭을 방지한다.

    - elements: 짧은 요소 ID → 해당 요소가 속한 Playwright 프레임.
    - ids: 짧은 ID와 긴 내부 ID의 양방향 변환기.
    - inflight: 아직 완료되지 않은 네트워크 요청 키. 비어 있고 last_network 이후
      일정 시간이 지나면 네트워크가 안정된 것으로 본다.
    - main_frame_id: 주 문서 프레임의 ID. compact 출력에서 frame 생략 기준이다.
    """

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
    ids: IdRegistry = field(default=None)
    inflight: set = field(default_factory=set)
    last_network: float = field(default_factory=time.monotonic)
    main_frame_id: str | None = None

    def __post_init__(self):
        """ID 레지스트리는 탭 ID를 접두사로 쓰므로 생성 직후 만든다."""
        if self.ids is None:
            self.ids = IdRegistry(self.id)


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
        # 프로필 이름 → Chromium 사용자 데이터 디렉터리. 프로세스/잠금 추적의 기준이다.
        self.profile_dirs: dict[str, Path] = {}
        self.close_report: dict | None = None
        # 실제로 실행한 브라우저(번들/시스템)와 대체 사유. 첫 프로필 실행 때 채운다.
        self.browser_info: dict | None = None
        # v0.3.0: 프로세스 기준 실행 엔진 정보(runtime)와 "첫 응답에 전체를 실었는지" 표시. 이후 응답은 runtime_id만 싣는다.
        self.runtime: dict | None = None
        self.runtime_announced = False
        # 프로필별 적용 설정(locale/timezone_id/viewport/mobile/device_scale_factor). profile.list와 capabilities가 돌려준다.
        self.settings: dict[str, dict] = {}

    def emit(self, kind: str, **data) -> None:
        """시간/증가 번호가 있는 이벤트를 저장한다. 웹 원문은 항상 데이터 필드에만 둔다."""
        self.sequence += 1
        self.events.append({"seq": self.sequence, "time": time.time(), "type": kind, **data})

    def event_view(self, since: int = 0) -> dict:
        """누락 여부와 다음 커서를 포함하여 비동기 이벤트를 재조회 가능하게 제공한다."""
        return {"events": [e for e in self.events if e["seq"] > since], "cursor": self.sequence,
                "events_omitted": bool(self.events and since < self.events[0]["seq"] - 1)}

    # 응답 요약에서 URL까지 보여줄 가치가 있는 오류성 이벤트.
    ERROR_EVENTS = {"network_failed", "not_found", "authentication_required", "server_error", "http_error",
                    "script_error", "browser_crashed", "download_failed", "background_error"}

    def activity_view(self, since: int = 0, tab_id: str | None = None, detail: int = 5) -> dict:
        """이벤트 원문 대신 종류별 집계를 돌려준다(v0.2.0).

        탐색 한 번에 광고/추적 iframe 탐색과 요청 실패 이벤트가 수십 개 생기고 각 URL이 수백 자에
        이르러 응답의 절반 이상을 차지했다. 집계는 종류별 개수와 주 문서 탐색 정보, 오류성
        이벤트 몇 건(URL은 호스트+경로로 축약)만 담고, 원문은 `events` 명령(since=cursor)으로
        조회한다. `tab_id`가 있으면 그 탭과 탭 무관 이벤트만 센다.
        """
        selected = [e for e in self.events if e["seq"] > since and (tab_id is None or e.get("tab") in (tab_id, None))]
        by_type = {}
        for event in selected:
            by_type[event["type"]] = by_type.get(event["type"], 0) + 1
        errors = []
        for event in selected:
            if event["type"] in self.ERROR_EVENTS and len(errors) < detail:
                entry = {"type": event["type"]}
                if event.get("url"):
                    entry["url"] = short_url(event["url"])
                if event.get("status"):
                    entry["status"] = event["status"]
                if event.get("message"):
                    entry["message"] = str(event["message"])[:120]
                errors.append(entry)
        navigation = [e for e in selected if e["type"] in {"navigation", "redirect"}]
        return {"since": since, "cursor": self.sequence, "events": len(selected), "by_type": by_type,
                "errors": errors, "errors_total": sum(by_type.get(t, 0) for t in self.ERROR_EVENTS),
                "redirects": by_type.get("redirect", 0), "frame_navigations": by_type.get("frame_navigation", 0),
                "final_url": navigation[-1]["url"] if navigation else None,
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

    def load_settings(self, profile_dir: Path) -> dict:
        """프로필 디렉터리에 저장된 이전 설정을 읽는다. 없거나 손상됐으면 빈 사전이다."""
        try:
            data = json.loads((profile_dir / PROFILE_SETTINGS).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_settings(self, profile_dir: Path, settings: dict) -> None:
        """적용한 설정을 프로필 디렉터리에 저장한다. 저장 실패는 세션 동작에 영향을 주지 않는다."""
        try:
            (profile_dir / PROFILE_SETTINGS).write_text(json.dumps({**settings, "version": 1}, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    async def create_profile(self, name: str = "default", **options) -> dict:
        """프로필별 Chromium 저장소를 열어 쿠키/로그인을 격리하고 재실행 시 복원한다.

        v0.3.0: `locale`/`timezone_id`를 받아 검증·적용하고, 명시하지 않은 설정은 이전 실행이 저장한 값
        (`aribrowser-profile.json`) → 세션 기본값(Config) 순으로 쓴다. 응답에 실제 적용값(`settings`)과
        실행 엔진 정보(`runtime`)를 싣는다.
        """
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise BrowserError("INVALID_ARGUMENT", "프로필 이름은 영문/숫자/_/- 1~64자입니다.")
        if name in self.contexts:
            raise BrowserError("PROFILE_EXISTS", "이미 열린 프로필입니다.", profile=name)
        await self.start()
        profile_dir = self.config.data_dir / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.chmod(0o700)
        stored = self.load_settings(profile_dir)
        # 명시한 값 > 저장된 값 > 세션 기본값. 저장된 값도 다시 검증해 손상된 파일이 실행을 깨지 않게 한다.
        policy = options.get("media_policy", self.config.media_policy)
        if policy not in {"defer", "allow"}:
            raise BrowserError("INVALID_ARGUMENT", "media_policy는 defer 또는 allow입니다.")
        viewport = options.get("viewport", stored.get("viewport") or {"width": self.config.width, "height": self.config.height})
        if not isinstance(viewport, dict) or any(type(viewport.get(k)) is not int or not 240 <= viewport[k] <= 7680 for k in ("width", "height")):
            raise BrowserError("INVALID_ARGUMENT", "화면 너비/높이는 240~7680 정수입니다.")
        viewport = {"width": viewport["width"], "height": viewport["height"]}
        mobile = options.get("mobile", stored.get("mobile", self.config.mobile))
        scale = options.get("device_scale_factor", stored.get("device_scale_factor", 1))
        if not isinstance(mobile, bool):
            raise BrowserError("INVALID_ARGUMENT", "mobile은 true/false입니다.")
        if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not 0.5 <= scale <= 4:
            raise BrowserError("INVALID_ARGUMENT", "device_scale_factor는 0.5~4 사이의 수입니다.")
        try:
            locale = validate_locale(options.get("locale", stored.get("locale", self.config.locale)))
            timezone_id = validate_timezone(options.get("timezone_id", stored.get("timezone_id", self.config.timezone_id)))
        except BrowserError as error:
            if "locale" in options or "timezone_id" in options:
                raise
            # 저장된 값이 잘못됐으면(손상된 파일) 무시하고 세션 기본값으로 연다.
            locale = validate_locale(self.config.locale)
            timezone_id = validate_timezone(self.config.timezone_id)
            self.emit("profile_settings_ignored", profile=name, reason=str(error))
        settings = {"locale": locale, "timezone_id": timezone_id, "viewport": viewport, "mobile": mobile,
                    "device_scale_factor": scale, "media_policy": policy}
        options = {**options, "mobile": mobile, "device_scale_factor": scale, "locale": locale, "timezone_id": timezone_id}
        # 비정상 종료가 남긴 잠금은 복구하고, 살아 있는 프로세스의 잠금은 충돌로 보고한다.
        lock = processes.release_stale_lock(profile_dir)
        if lock["present"] and not lock["stale"]:
            raise BrowserError("PROFILE_LOCKED", "다른 프로세스가 이 프로필을 사용 중입니다.", profile=name,
                               holder_pid=lock.get("holder_pid"), fix=sandbox.DOCTOR_CODES["PROFILE_LOCKED"])
        if lock["removed"]:
            self.emit("profile_lock_recovered", profile=name, removed=lock["removed"], holder_pid=lock.get("holder_pid"))
        context = await self._launch(name, profile_dir, viewport, options, policy)
        self.contexts[name] = context
        self.allowed[name] = set()
        self.policies[name] = policy
        self.profile_dirs[name] = profile_dir
        self.settings[name] = settings
        self.save_settings(profile_dir, settings)
        context.set_default_timeout(self.config.timeout_ms)
        for script in ("media.js", "activity.js"):
            await context.add_init_script(script=Path(__file__).with_name(script).read_text(encoding="utf-8"))
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
        await self._describe_runtime(context, profile_dir)
        return {"profile": name, "persistent": True, "media_policy": policy, "settings": settings,
                "locale": locale, "timezone_id": timezone_id, "tabs": await self.list_tabs()}

    def announce_runtime(self) -> dict | None:
        """첫 응답에는 runtime 전체를, 이후에는 반복 payload를 줄이기 위해 `{runtime_id}`만 돌려준다."""
        if not self.runtime:
            return None
        if not self.runtime_announced:
            self.runtime_announced = True
            return self.runtime
        return {"runtime_id": self.runtime["runtime_id"]}

    async def _describe_runtime(self, context: BrowserContext, profile_dir: Path) -> None:
        """실제 실행 엔진을 프로세스 기준으로 확인해 `runtime`을 채운다(v0.3.0).

        실행 파일 이름(launcher)만으로 엔진을 추정하지 않는다. CDP `Browser.getVersion`이 돌려주는 제품 문자열
        (`Chrome/146.0.7680.80`, `HeadlessChrome/153.0.8010.12`)에서 버전을 읽고, Linux에서는 브라우저 주 프로세스의
        `/proc/<pid>/exe`로 실제 실행 파일을 확인한다. `sandbox_active`는 "--no-sandbox 없이 정상 시작했다"는 사실에서
        오고(Chromium은 사용 가능한 샌드박스가 없으면 시작하지 못한다), 렌더러의 Seccomp 필터 상태를 `sandbox_evidence`로
        덧붙인다(실행 직후라 렌더러가 아직 없으면 null).
        """
        info = dict(self.browser_info or {})
        # 같은 실행 파일/종류면 프로필마다 다시 조사하지 않는다(프로필마다 Chromium 프로세스는 따로지만 엔진은 같다).
        if self.runtime and self.runtime.get("executable") == info.get("executable") and self.runtime.get("kind") == info.get("kind"):
            return
        product = user_agent = None
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            session = await context.new_cdp_session(page)
            try:
                version = await asyncio.wait_for(session.send("Browser.getVersion"), 5)
            finally:
                await session.detach()
            product = version.get("product")
            user_agent = version.get("userAgent")
        except (PlaywrightError, asyncio.TimeoutError):
            pass
        resolved = None
        if sys.platform.startswith("linux"):
            for process in processes.chromium_processes(profile_dir):
                if process["type"] == "browser":
                    try:
                        resolved = os.readlink(f"/proc/{process['pid']}/exe")
                    except OSError:
                        resolved = None
                    break
        evidence = sandbox.seccomp_evidence(profile_dir) if sys.platform.startswith("linux") else None
        self.runtime = {
            "runtime_id": "rt" + hashlib.sha1(f"{info.get('executable')}|{product}".encode()).hexdigest()[:6],
            "kind": info.get("kind"), "product": product,
            "version": product.split("/", 1)[1] if product and "/" in product else sandbox.chromium_version(info.get("executable")),
            "executable": info.get("executable"), "resolved_executable": resolved or info.get("executable"),
            "fallback_from": info.get("fallback_from"), "sandbox_active": not self.config.no_sandbox,
            "sandbox_source": "none" if self.config.no_sandbox else "setuid_helper" if info.get("sandbox_helper") else "namespace_or_profile",
            "sandbox_evidence": {"seccomp_filtered": evidence["seccomp_filtered"]} if evidence else None,
            "headless": self.config.headless, "user_agent": user_agent,
        }
        self.browser_info = {**info, "version": self.runtime["version"], "product": product,
                             "resolved_executable": self.runtime["resolved_executable"], "runtime_id": self.runtime["runtime_id"]}

    def launch_candidates(self) -> list[dict]:
        """Config.browser 정책에 따라 시도할 실행 파일 후보를 우선순위대로 만든다.

        - executable_path가 있으면 그 경로 하나만 쓴다.
        - bundled: Playwright가 설치한 번들 Chromium 하나.
        - system: 설치된 시스템 브라우저만(없으면 SYSTEM_BROWSER_MISSING).
        - auto: 번들 Chromium 다음에 시스템 브라우저(snap은 마지막).
        """
        if self.config.executable_path:
            return [{"kind": "custom", "executable_path": self.config.executable_path}]
        bundled = None
        try:
            bundled = self.playwright.chromium.executable_path
        except Exception:
            bundled = None
        candidates = []
        policy = self.config.browser
        if policy in {"auto", "bundled"}:
            # v0.3.0: 번들 바이너리인데 Playwright가 번들 밖의 Chromium을 골랐으면 번들 안의 같은 바이너리를 명시한다.
            rebased = sandbox.rebase_into_bundle(bundled, sandbox.bundle_browsers_dir(), self.config.headless)
            if rebased:
                self.emit("browser_rebased", from_executable=bundled, executable=rebased)
                candidates.append({"kind": "bundled", "executable_path": rebased, "resolved": rebased})
            else:
                candidates.append({"kind": "bundled", "executable_path": None, "resolved": bundled})
        if policy in {"auto", "system"}:
            for found in sandbox.system_browsers():
                candidates.append({"kind": found["kind"], "executable_path": found["path"], "resolved": found["resolved"], "snap": found["snap"]})
        if policy == "system" and len(candidates) == 0:
            raise BrowserError("SYSTEM_BROWSER_MISSING", "설치된 시스템 브라우저를 찾지 못했습니다.", fix=sandbox.DOCTOR_CODES["SYSTEM_BROWSER_MISSING"])
        return candidates

    async def _launch(self, name: str, profile_dir: Path, viewport: dict, options: dict, policy: str) -> BrowserContext:
        """후보를 순서대로 실행한다. 번들 Chromium의 샌드박스/의존성 실패만 시스템 브라우저로 대체한다.

        대체는 `--no-sandbox`가 아니라 "이미 샌드박스가 동작하는 다른 Chromium"으로 바꾸는 것이다.
        모든 후보가 실패하면 첫 후보(번들)의 오류를 돌려주고 details.tried에 시도 내역을 담는다.
        """
        candidates = self.launch_candidates()
        tried = []
        first_error = None
        for candidate in candidates:
            executable = candidate["executable_path"] or candidate.get("resolved")
            env = {**os.environ, **sandbox.launch_env(executable)}
            try:
                context = await self.playwright.chromium.launch_persistent_context(
                    str(profile_dir), headless=self.config.headless,
                    executable_path=candidate["executable_path"],
                    chromium_sandbox=not self.config.no_sandbox,
                    viewport=viewport, is_mobile=options.get("mobile", self.config.mobile),
                    has_touch=options.get("mobile", self.config.mobile),
                    device_scale_factor=options.get("device_scale_factor", 1),
                    locale=options.get("locale"), timezone_id=options.get("timezone_id"),
                    accept_downloads=True, service_workers="block" if policy == "defer" else "allow",
                    args=["--autoplay-policy=user-gesture-required"], env=env,
                )
            except PlaywrightError as error:
                # 시간대/로캘 거부는 샌드박스 문제가 아니라 잘못된 인수다(tz 데이터가 없는 호스트의 문법 검사 보완).
                if "timezone" in str(error).lower() and options.get("timezone_id"):
                    raise BrowserError("INVALID_ARGUMENT", "Chromium이 timezone_id를 거부했습니다.", timezone_id=options.get("timezone_id"),
                                       reason=sandbox.significant_line(str(error))) from error
                # 같은 "sandboxing failed" 메시지라도 root 실행과 AppArmor 제한을 구분해 돌려준다.
                browser_error = sandbox.launch_error(str(error), sandbox.launch_context(executable))
                tried.append({"kind": candidate["kind"], "executable": executable, "code": browser_error.code})
                first_error = first_error or browser_error
                recoverable = browser_error.code.startswith("SANDBOX_") or browser_error.code in {"CHROMIUM_DEPENDENCIES_MISSING", "CHROMIUM_MISSING"}
                if candidate["kind"] == "bundled" and recoverable and len(candidates) > 1:
                    self.emit("browser_fallback", reason=browser_error.code, fix=browser_error.details.get("fix"))
                    continue
                if candidate["kind"] != "bundled" and len(candidates) > 1:
                    continue
                break
            if self.browser_info is None or self.browser_info.get("kind") != candidate["kind"]:
                self.browser_info = {"kind": candidate["kind"], "executable": executable,
                                     "fallback_from": tried[0]["code"] if tried else None,
                                     "sandbox_helper": bool(env.get("CHROME_DEVEL_SANDBOX"))}
                if tried:
                    self.emit("browser_selected", browser=candidate["kind"], executable=executable, fallback_from=tried[0]["code"])
            return context
        assert first_error is not None
        first_error.details["tried"] = tried
        raise first_error

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
        tab = self.tabs.get(tab_id)
        if tab:
            tab.inflight.add(key)
            tab.last_network = time.monotonic()

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
        key = self.request_ids.get(request)
        record = self.resources.get(key)
        tab = self.tabs.get(record["tab"]) if record else None
        if tab:
            tab.inflight.discard(key)
            tab.last_network = time.monotonic()
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
        """프로필 브라우저를 닫고 Chromium 프로세스 종료와 잠금 해제를 확인해 보고한다.

        사용자 저장소와 다운로드 파일은 보존한다. 정상 종료 뒤에도 남은 프로세스는
        종료 요청/강제 종료하고, 그 수를 응답에 포함한다.
        """
        context = self.contexts.get(profile)
        if not context:
            raise BrowserError("PROFILE_NOT_FOUND", "열린 프로필이 없습니다.", profile=profile)
        profile_dir = self.profile_dirs.get(profile, self.config.data_dir / "profiles" / profile)
        before = processes.chromium_processes(profile_dir)
        try:
            await context.close()
        except PlaywrightError:
            pass  # 이미 충돌/종료된 브라우저는 아래 프로세스 정리로 마무리한다.
        self.contexts.pop(profile, None)
        self.allowed.pop(profile, None)
        self.policies.pop(profile, None)
        self.profile_dirs.pop(profile, None)
        self.settings.pop(profile, None)
        remaining = await processes.wait_for_exit(profile_dir, timeout=3)
        forced = processes.terminate([p["pid"] for p in remaining]) if remaining else None
        lock = processes.release_stale_lock(profile_dir)
        still = processes.chromium_processes(profile_dir)
        return {"closed": profile, "processes": {"before": len(before), "exited": len(before) - len(remaining),
                                                 "forced": forced["terminated"] if forced else 0,
                                                 "remaining": [p["pid"] for p in still]},
                "lock": {"released": not lock["present"] or bool(lock["removed"]), "path": lock["path"],
                         "removed": lock["removed"], "remaining": lock["present"] and not lock["removed"]}}

    async def close(self) -> dict:
        """진행 중인 다운로드에 짧은 완료 기회를 준 뒤 모든 엔진 자원을 해제하고 정리 결과를 돌려준다."""
        self.closing = True
        if self.tasks:
            _, pending = await asyncio.wait(self.tasks, timeout=5)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        reports = []
        for name in list(self.contexts):
            reports.append(await self.close_profile(name))
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        remaining = [pid for report in reports for pid in report["processes"]["remaining"]]
        self.close_report = {
            "closed": True, "profiles": reports,
            "processes": {"terminated": sum(r["processes"]["exited"] + r["processes"]["forced"] for r in reports),
                          "forced": sum(r["processes"]["forced"] for r in reports), "remaining": len(remaining),
                          "remaining_pids": remaining},
            "locks": {"released": all(r["lock"]["released"] for r in reports),
                      "remaining": [r["closed"] for r in reports if r["lock"]["remaining"]]},
        }
        return self.close_report
