"""Chromium 샌드박스 실행 환경의 진단(doctor), 보안 정책 설치, 적용 상태 검증, 시스템 브라우저 탐색을 담당한다.

Ubuntu 24.04부터 AppArmor가 비특권 user namespace 생성을 제한하므로
(`kernel.apparmor_restrict_unprivileged_userns=1`) 정책 없는 번들 Chromium은
`Chromium sandboxing failed!`로 종료된다. 같은 메시지는 root 실행에서도 나오므로
원인을 구분해 서로 다른 오류 코드를 돌려준다.

v0.1.0의 AppArmor 정책은 설치 디렉터리(브라우저 루트) 경로에 붙어 있어 AriBrowser를
새 경로에 풀 때마다 다시 설치해야 했다. v0.2.0은 "한 번 설치하면 어떤 경로의 AriBrowser든
사용"을 목표로 다음 세 가지를 제공한다.

- `helper`(기본): Chromium이 제공하는 setuid 샌드박스 도우미(`chrome_sandbox`)를 고정 경로
  `/usr/local/lib/aribrowser/chrome-sandbox`(root:root, 4755)에 한 번 설치한다. 모든 AriBrowser
  실행은 이 경로가 유효하면 `CHROME_DEVEL_SANDBOX`로 Chromium에 알려주므로 설치 경로·Chromium
  리비전·사용자와 무관하게 동작한다. user namespace 제한과 AppArmor 유무에도 영향을 받지 않는다.
  (Google Chrome의 .deb 패키지가 쓰는 것과 같은 방식이며 setuid 샌드박스 API는 동결되어 있다.)
- `apparmor`: `userns` 허용 프로필을 `/etc/apparmor.d/aribrowser-chromium`에 설치한다. v0.2.0의
  프로필은 특정 설치 경로 대신 모든 사용자의 Playwright 캐시(`/home/*/.cache/ms-playwright`)와
  AriBrowser 배포 디렉터리 구조(`**/_internal/playwright/driver/package/.local-browsers`)에
  동시에 붙으므로 새 경로에 풀어도 다시 설치할 필요가 없다.
- 시스템 브라우저 대체(`--browser auto`): 번들 Chromium이 샌드박스 문제로 실행되지 않고 Google
  Chrome/Chromium/Edge/Brave가 설치돼 있으면 그 브라우저로 실행한다. 배포판이 이미 해당
  브라우저에 userns를 허용하거나 자체 setuid 도우미를 갖추고 있어 별도 설치가 필요 없다.

어떤 경우에도 `--no-sandbox`를 자동으로 사용하지 않는다.
"""

import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .errors import BrowserError

PROFILE_NAME = "aribrowser-chromium"
PROFILE_PATH = Path("/etc/apparmor.d") / PROFILE_NAME
USERNS_KNOB = Path("/proc/sys/kernel/apparmor_restrict_unprivileged_userns")
ROOT_MARKER = "# aribrowser-root: "
ROOTS_MARKER = "# aribrowser-roots: "
# 공용 setuid 샌드박스 도우미의 고정 경로. 설치 경로와 무관하게 모든 AriBrowser가 공유한다.
HELPER_DIR = Path("/usr/local/lib/aribrowser")
SHARED_HELPER = HELPER_DIR / "chrome-sandbox"
# 배포 디렉터리 안의 Chromium 위치. 어떤 경로에 풀어도 이 꼬리 구조는 같다.
BUNDLE_TAIL = "_internal/playwright/driver/package/.local-browsers"
# AppArmor 정책이 항상 포함하는 경로 패턴(모든 사용자 캐시 + 모든 배포 디렉터리).
STANDARD_ROOT_PATTERNS = ["/home/*/.cache/ms-playwright", "/root/.cache/ms-playwright", f"/**/{BUNDLE_TAIL}"]

# doctor와 실행 오류가 돌려주는 코드와 조치 안내. 실패 지점별로 구분한다.
DOCTOR_CODES = {
    "CHROMIUM_MISSING": "번들 Chromium 실행 파일을 찾지 못했습니다. 배포 파일을 전체 해제했는지 확인하세요.",
    "CHROMIUM_DEPENDENCIES_MISSING": "Chromium 공유 라이브러리가 없습니다. Linux에서는 `python -m playwright install-deps chromium` 또는 README의 패키지를 설치하세요.",
    "SANDBOX_ROOT_USER": "root 사용자는 Chromium 샌드박스를 사용할 수 없습니다. 일반 사용자로 실행하세요. 컨테이너 전용 격리 환경에서만 --no-sandbox를 고려하세요.",
    "SANDBOX_USERNS_RESTRICTED": "이 커널은 비특권 user namespace를 제한합니다. `sudo ./aribrowser --install-sandbox`로 공용 샌드박스 도우미를 한 번만 설치하면 설치 경로와 무관하게 동작합니다. 시스템에 Chrome/Chromium이 있으면 `--browser auto`가 그 브라우저를 대신 사용합니다.",
    "SANDBOX_HELPER_INVALID": "공용 샌드박스 도우미가 있지만 root 소유 4755가 아닙니다. `sudo ./aribrowser --install-sandbox`로 다시 설치하세요.",
    "SANDBOX_PROFILE_STALE": "설치된 AppArmor 정책이 현재 Chromium 경로를 포함하지 않습니다. `sudo ./aribrowser --install-sandbox`로 공용 도우미를 설치하거나 `--sandbox-method apparmor`로 정책을 갱신하세요.",
    "SANDBOX_PROFILE_NOT_LOADED": "AppArmor 정책 파일은 있지만 커널에 적재되지 않았습니다. `sudo apparmor_parser -r /etc/apparmor.d/aribrowser-chromium`을 실행하세요.",
    "SANDBOX_LAUNCH_FAILED": "Chromium 샌드박스 실행에 실패했습니다. doctor 출력의 message를 확인하세요.",
    "DISPLAY_MISSING": "창 표시(--headed)에 필요한 DISPLAY/WAYLAND_DISPLAY가 없습니다. headless로 실행하거나 디스플레이를 설정하세요.",
    "PROFILE_LOCKED": "다른 프로세스가 프로필을 사용 중입니다. 다른 data-dir/profile을 쓰거나 --cleanup으로 잔여 프로세스를 정리하세요.",
    "BROWSER_LAUNCH_FAILED": "Chromium을 시작하지 못했습니다. doctor 출력의 message를 확인하세요.",
    "SYSTEM_BROWSER_MISSING": "--browser system을 지정했지만 설치된 Chrome/Chromium/Edge/Brave를 찾지 못했습니다. --browser auto 또는 --chromium <경로>를 사용하세요.",
}


def is_root() -> bool:
    """POSIX에서 root 여부. Windows는 항상 False다."""
    return hasattr(os, "geteuid") and os.geteuid() == 0


def bundle_browsers_dir() -> Path | None:
    """PyInstaller 번들 안의 Playwright 브라우저 디렉터리(`_internal/playwright/driver/package/.local-browsers`).

    번들 바이너리가 아니거나 그 디렉터리가 없으면 None이다. v0.3.0: 번들 바이너리는 호스트의 Playwright
    캐시나 PLAYWRIGHT_BROWSERS_PATH와 무관하게 이 디렉터리의 Chromium만 쓴다(설치 경로 밖의 다른 리비전이
    실행되는 것을 막는다). 다른 실행 파일은 `--chromium`으로 지정한다.
    """
    if not getattr(sys, "frozen", False):
        return None
    internal = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent / "_internal"))
    local = internal / "playwright" / "driver" / "package" / ".local-browsers"
    return local if local.is_dir() else None


def rebase_into_bundle(executable: str | None, local: Path | None, headless: bool = True) -> str | None:
    """Playwright가 번들 밖(사용자 캐시 등)의 Chromium을 골랐으면 번들 디렉터리의 같은 상대 경로로 바꾼다.

    `<root>/chromium-1243/chrome-linux64/chrome` → `<local>/chromium-1243/chrome-linux64/chrome`. headless면 같은
    리비전의 chrome-headless-shell을 우선한다(Playwright가 executable_path 없이 실행할 때 고르는 것과 같은 바이너리이며,
    같은 리비전이 없으면 번들의 최신 headless shell). 번들 안에 쓸 바이너리가 없으면 None(원래 선택을 유지).
    """
    if not executable or local is None:
        return None
    path = Path(executable)
    if local in path.parents:
        return None
    layout = chromium_layout(executable)
    try:
        relative = path.relative_to(layout["browsers_root"])
    except ValueError:
        return None
    candidate = local / relative
    if headless:
        shell = chromium_layout(str(candidate)).get("headless_shell")
        if shell and Path(shell).is_file() and local in Path(shell).parents:
            return shell
    return str(candidate) if candidate.is_file() else None


def chromium_layout(executable_path: str) -> dict:
    """실행 파일 경로에서 Chromium 디렉터리, 브라우저 루트, headless shell 경로를 계산한다."""
    chromium = Path(executable_path).resolve()
    browsers_root = None
    for parent in chromium.parents:
        if parent.name.startswith("chromium-") or parent.name.startswith("chromium_headless_shell-"):
            browsers_root = parent.parent
            break
    if browsers_root is None:
        browsers_root = chromium.parent
    # 같은 리비전의 headless shell을 우선하고, 없으면 가장 최신 리비전을 쓴다.
    revision = next((parent.name.split("-", 1)[1] for parent in chromium.parents if parent.name.startswith("chromium-")), None)
    shells = [s for s in sorted(browsers_root.glob("chromium_headless_shell-*/*/chrome-headless-shell*"))
              if s.is_file() and not s.name.endswith(".pak")]
    same = [s for s in shells if revision and f"chromium_headless_shell-{revision}" in s.parts]
    chosen = (same or shells)[-1] if (same or shells) else None
    return {"chromium": str(chromium), "exists": chromium.is_file(), "directory": str(chromium.parent),
            "browsers_root": str(browsers_root), "headless_shell": str(chosen) if chosen else None,
            "bundled": BUNDLE_TAIL in str(browsers_root)}


def registry_versions() -> dict[str, str]:
    """Playwright 브라우저 레지스트리(browsers.json)에서 디렉터리 이름 → 브라우저 버전 표를 만든다.

    예: {"chromium-1243": "153.0.8010.12", "chromium_headless_shell-1243": "153.0.8010.12"}.
    실행 파일을 띄우지 않고 버전을 알 수 있어 빠르고, Windows처럼 GUI 실행 파일이
    `--version`을 콘솔에 출력하지 않는 환경에서도 동작한다.
    """
    try:
        import playwright

        registry = Path(playwright.__file__).parent / "driver" / "package" / "browsers.json"
        data = json.loads(registry.read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return {}
    versions = {}
    for browser in data.get("browsers", []):
        if browser.get("browserVersion"):
            versions[f"{browser['name'].replace('-', '_')}-{browser['revision']}"] = browser["browserVersion"]
    return versions


def chromium_version(executable: str | None) -> str | None:
    """번들 Chromium의 버전을 읽는다. 레지스트리를 우선하고, 없으면 `--version` 실행 결과를 쓴다."""
    if not executable or not Path(executable).is_file():
        return None
    versions = registry_versions()
    for parent in Path(executable).resolve().parents:
        if parent.name in versions:
            return f"Chromium {versions[parent.name]}"
    if sys.platform == "win32":
        return None  # Windows의 chrome.exe는 GUI 서브시스템이라 --version을 콘솔에 쓰지 않는다.
    try:
        output = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=15,
                                env={**os.environ, "DISPLAY": os.environ.get("DISPLAY", "")})
        return (output.stdout or output.stderr).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- 시스템 브라우저
# 플랫폼별 시스템 브라우저 후보. 순서가 우선순위다(Chrome → Chromium → Edge → Brave).
SYSTEM_BROWSERS = {
    "linux": [
        ("chrome", ["google-chrome-stable", "google-chrome", "/opt/google/chrome/chrome"]),
        ("chromium", ["chromium", "chromium-browser", "/usr/lib/chromium/chromium", "/usr/lib/chromium-browser/chromium-browser"]),
        ("msedge", ["microsoft-edge-stable", "microsoft-edge", "/opt/microsoft/msedge/msedge"]),
        ("brave", ["brave-browser", "/opt/brave.com/brave/brave"]),
    ],
    "darwin": [
        ("chrome", ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]),
        ("chromium", ["/Applications/Chromium.app/Contents/MacOS/Chromium"]),
        ("msedge", ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]),
        ("brave", ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]),
    ],
    "win32": [
        ("chrome", [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                    r"%LocalAppData%\Google\Chrome\Application\chrome.exe"]),
        ("chromium", [r"%LocalAppData%\Chromium\Application\chrome.exe"]),
        ("msedge", [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"]),
        ("brave", [r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
                   r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe"]),
    ],
}


def system_browsers() -> list[dict]:
    """설치된 Chromium 계열 시스템 브라우저를 우선순위대로 나열한다.

    Linux의 명령 이름은 PATH에서 찾고 래퍼 스크립트(`google-chrome`)는 그대로 실행한다.
    Snap 패키지(`/snap/bin/chromium`)는 홈의 숨김 디렉터리(`~/.aribrowser`)에 접근하지 못해
    프로필을 열 수 없으므로 `snap=true`로 표시하고 자동 대체 순서에서 뒤로 보낸다.
    """
    platform_key = "win32" if sys.platform == "win32" else "darwin" if sys.platform == "darwin" else "linux"
    found, seen = [], set()
    for kind, candidates in SYSTEM_BROWSERS.get(platform_key, []):
        for candidate in candidates:
            expanded = os.path.expandvars(candidate)
            path = shutil.which(expanded) if not os.path.isabs(expanded) else (expanded if Path(expanded).is_file() else None)
            if not path:
                continue
            resolved = str(Path(path).resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            snap = resolved.startswith("/snap/") or "/snap/" in resolved
            found.append({"kind": kind, "path": path, "resolved": resolved, "snap": snap})
    found.sort(key=lambda item: item["snap"])
    return found


# ---------------------------------------------------------------- setuid 도우미
def helper_status(path: Path) -> dict:
    """setuid 샌드박스 도우미 하나의 설치 상태(존재/소유자/모드)를 확인한다."""
    if not path.exists():
        return {"installed": False, "path": str(path), "exists": False}
    stat = path.stat()
    setuid = bool(stat.st_mode & 0o4000)
    valid = stat.st_uid == 0 and setuid and (stat.st_mode & 0o777) == 0o755
    return {"installed": valid, "path": str(path), "exists": True, "owner_uid": stat.st_uid,
            "mode": oct(stat.st_mode & 0o7777), "setuid": setuid, "size": stat.st_size}


def shared_helper_status() -> dict:
    """고정 경로의 공용 도우미 상태. Linux가 아니면 적용 대상이 아니다."""
    if not sys.platform.startswith("linux"):
        return {"applicable": False, "installed": False, "path": str(SHARED_HELPER)}
    return {"applicable": True, **helper_status(SHARED_HELPER)}


def suid_status(directory: str | None) -> dict:
    """Chromium 디렉터리 안의 setuid 도우미(chrome-sandbox) 상태. v0.1.0 방식과의 호환용이다."""
    if not sys.platform.startswith("linux") or not directory:
        return {"applicable": sys.platform.startswith("linux"), "installed": False}
    helper = Path(directory) / "chrome-sandbox"
    source = Path(directory) / "chrome_sandbox"
    if not helper.exists():
        return {"applicable": True, "installed": False, "path": str(helper), "source_available": source.exists()}
    return {"applicable": True, **helper_status(helper), "source_available": source.exists()}


def find_helper_source(executable_path: str | None) -> Path | None:
    """설치할 도우미의 원본(`chrome_sandbox`)을 번들 Chromium 디렉터리에서 찾는다."""
    if not executable_path:
        return None
    layout = chromium_layout(executable_path)
    direct = Path(layout["directory"]) / "chrome_sandbox"
    if direct.is_file():
        return direct
    for candidate in sorted(Path(layout["browsers_root"]).glob("chromium-*/*/chrome_sandbox")):
        if candidate.is_file():
            return candidate
    return None


def launch_env(executable_path: str | None) -> dict:
    """사용 가능한 setuid 도우미가 있으면 Chromium에 알려주는 환경 변수를 만든다.

    공용 도우미(고정 경로)를 우선하고, 없으면 v0.1.0 방식의 디렉터리 안 도우미를 쓴다.
    user namespace가 허용된 환경에서는 Chromium이 namespace 샌드박스를 우선하므로
    이 변수를 항상 넘겨도 해가 없다.
    """
    if not sys.platform.startswith("linux"):
        return {}
    shared = shared_helper_status()
    if shared.get("installed"):
        return {"CHROME_DEVEL_SANDBOX": shared["path"]}
    if not executable_path:
        return {}
    status = suid_status(str(Path(executable_path).parent))
    if status.get("installed"):
        return {"CHROME_DEVEL_SANDBOX": status["path"]}
    # headless shell 디렉터리에는 도우미가 없으므로 전체 Chromium 디렉터리의 도우미를 공유한다.
    layout = chromium_layout(executable_path)
    for candidate in Path(layout["browsers_root"]).glob("chromium-*/*/chrome-sandbox"):
        if suid_status(str(candidate.parent)).get("installed"):
            return {"CHROME_DEVEL_SANDBOX": str(candidate)}
    return {}


def install_helper(executable_path: str | None) -> dict:
    """공용 setuid 도우미를 고정 경로에 설치한다. root가 아니면 수동 설치 명령을 돌려준다."""
    if not sys.platform.startswith("linux"):
        return {"status": "not_applicable", "reason": "setuid 샌드박스 도우미는 Linux 전용입니다."}
    source = find_helper_source(executable_path)
    if not source:
        return {"status": "failed", "reason": "chrome_sandbox 도우미를 번들 Chromium 디렉터리에서 찾지 못했습니다.", "executable": executable_path}
    if not is_root():
        return {"status": "needs_root", "method": "helper", "source": str(source), "path": str(SHARED_HELPER),
                "instructions": f"sudo mkdir -p {HELPER_DIR} && sudo cp {source} {SHARED_HELPER} && sudo chown root:root {SHARED_HELPER} && sudo chmod 4755 {SHARED_HELPER}"}
    HELPER_DIR.mkdir(parents=True, exist_ok=True)
    HELPER_DIR.chmod(0o755)
    shutil.copy2(source, SHARED_HELPER)
    os.chown(SHARED_HELPER, 0, 0)
    SHARED_HELPER.chmod(0o4755)
    return {"status": "installed", "method": "helper", "path": str(SHARED_HELPER), "source": str(source),
            "helper": shared_helper_status(), "scope": "이 호스트의 모든 AriBrowser 설치 경로와 Chromium 리비전에 적용됩니다."}


def uninstall_helper() -> dict:
    """공용 도우미를 제거한다."""
    if not SHARED_HELPER.exists():
        return {"status": "not_installed", "path": str(SHARED_HELPER)}
    if not is_root():
        return {"status": "needs_root", "instructions": f"sudo rm {SHARED_HELPER}"}
    SHARED_HELPER.unlink()
    try:
        HELPER_DIR.rmdir()
    except OSError:
        pass  # 다른 파일이 있으면 디렉터리는 남긴다.
    return {"status": "removed", "path": str(SHARED_HELPER)}


# ---------------------------------------------------------------- AppArmor
def _pattern_covers(pattern: str, path: str) -> bool:
    """AppArmor 경로 패턴(`*`, `**`)이 path 또는 그 조상 디렉터리에 해당하는지 검사한다."""
    import fnmatch
    import re

    def to_regex(glob: str) -> str:
        """AppArmor 글로브를 정규식으로 바꾼다. `**`는 여러 경로 요소, `*`는 한 요소 안에서만 일치한다."""
        out = ""
        index = 0
        while index < len(glob):
            if glob.startswith("**", index):
                out += ".*"
                index += 2
            elif glob[index] == "*":
                out += "[^/]*"
                index += 1
            else:
                out += re.escape(glob[index])
                index += 1
        return out

    regex = re.compile("^" + to_regex(pattern) + "(/.*)?$")
    if regex.match(path):
        return True
    return fnmatch.fnmatchcase(path, pattern + "/*") or fnmatch.fnmatchcase(path, pattern)


def profile_roots(content: str) -> list[str]:
    """정책 파일 머리글에서 적용 경로 패턴 목록을 읽는다. v0.1.0 형식(`aribrowser-root`)도 읽는다."""
    for line in content.splitlines():
        if line.startswith(ROOTS_MARKER):
            return [item.strip() for item in line[len(ROOTS_MARKER):].split(";") if item.strip()]
        if line.startswith(ROOT_MARKER):
            return [line[len(ROOT_MARKER):].strip()]
    return []


def apparmor_status(browsers_root: str | None, chromium_path: str | None = None) -> dict:
    """AppArmor 활성화, userns 제한, 정책 파일/적재 상태, 현재 경로 포함 여부를 읽는다."""
    if not sys.platform.startswith("linux"):
        return {"applicable": False}
    enabled_file = Path("/sys/module/apparmor/parameters/enabled")
    enabled = enabled_file.read_text().strip().startswith("Y") if enabled_file.exists() else False
    restrict = None
    if USERNS_KNOB.exists():
        try:
            restrict = int(USERNS_KNOB.read_text().strip())
        except (OSError, ValueError):
            restrict = None
    parser = shutil.which("apparmor_parser") or next((p for p in ("/sbin/apparmor_parser", "/usr/sbin/apparmor_parser") if Path(p).exists()), None)
    installed = PROFILE_PATH.exists()
    roots = profile_roots(PROFILE_PATH.read_text(encoding="utf-8", errors="replace")) if installed else []
    covers = None
    target = str(Path(chromium_path or browsers_root).resolve()) if (chromium_path or browsers_root) else None
    if installed and target:
        covers = any(_pattern_covers(pattern, target) for pattern in roots)
    loaded = None
    try:
        profiles = Path("/sys/kernel/security/apparmor/profiles").read_text()
        loaded = any(line.split(" ")[0] == PROFILE_NAME for line in profiles.splitlines())
    except OSError:
        loaded = None  # 일반 사용자는 적재 목록을 읽을 수 없다. 실제 실행 검사로 판단한다.
    return {"applicable": True, "enabled": enabled, "restrict_unprivileged_userns": restrict, "parser": parser,
            "profile_path": str(PROFILE_PATH), "profile_installed": installed, "profile_roots": roots,
            "profile_root": roots[0] if roots else None, "profile_covers_current": covers, "profile_loaded": loaded}


def userns_status() -> dict:
    """비특권 user namespace 생성 가능 여부를 자식 프로세스의 실제 unshare(2) 호출로 확인한다.

    `unshare` 명령은 Ubuntu가 전용 AppArmor 프로필로 허용하므로 그 결과는 번들 Chromium
    같은 unconfined 프로그램의 상황을 대표하지 않는다. 그래서 이 프로세스를 fork한
    자식(같은 confinement)에서 libc unshare(CLONE_NEWUSER)를 직접 호출한다.
    """
    if not sys.platform.startswith("linux"):
        return {"applicable": False}
    maximum = None
    try:
        maximum = int(Path("/proc/sys/user/max_user_namespaces").read_text().strip())
    except (OSError, ValueError):
        pass
    allowed = None
    try:
        import ctypes

        pid = os.fork()
        if pid == 0:  # 자식: 결과를 종료 코드로만 전달한다.
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.unshare(0x10000000) != 0:  # CLONE_NEWUSER
                    os._exit(1)
                # Ubuntu 24.04는 생성 자체는 허용하되 unprivileged_userns 프로필로 전환해
                # 네임스페이스 안의 권한을 제거한다. 이 경우 Chromium 샌드박스는 실패한다.
                label = Path("/proc/self/attr/current").read_text(errors="replace")
                os._exit(3 if "unprivileged_userns" in label else 0)
            except Exception:
                os._exit(2)
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
        allowed = True if code == 0 else False if code in {1, 3} else None
        restricted_by_apparmor = code == 3
    except (OSError, AttributeError):
        allowed, restricted_by_apparmor = None, None
    return {"applicable": True, "unprivileged_allowed": allowed, "confined_by_apparmor": restricted_by_apparmor,
            "max_user_namespaces": maximum}


def render_profile(browsers_root: str | None) -> str:
    """모든 사용자 캐시·배포 디렉터리·지정 루트 아래 Chromium/headless shell에 적용되는 정책 본문.

    v0.1.0은 지정 루트 하나에만 붙었지만 v0.2.0은 표준 패턴을 항상 포함해 새 경로에 풀어도
    재설치가 필요 없다. 공백이 있는 경로는 AppArmor 문법에 맞게 따옴표로 감싼다.
    """
    roots = list(STANDARD_ROOT_PATTERNS)
    if browsers_root:
        extra = str(Path(browsers_root).resolve())
        if not any(_pattern_covers(pattern, extra) for pattern in roots):
            roots.append(extra)
    attachment = "/{" + ",".join(root.lstrip("/") for root in roots) + "}/**/{chrome,chrome-headless-shell}"
    if " " in attachment:
        attachment = f'"{attachment}"'
    abi = "abi <abi/4.0>,\n" if Path("/etc/apparmor.d/abi/4.0").exists() else ""
    return (f"# AriBrowser {__version__} bundled Chromium sandbox policy. Generated by --install-sandbox.\n"
            f"{ROOTS_MARKER}{';'.join(roots)}\n"
            "# Ubuntu 24.04+ restricts unprivileged user namespaces; this profile allows them for\n"
            "# Chromium binaries inside Playwright caches and AriBrowser distribution directories\n"
            "# (any install path), like the distribution's own chrome/brave profiles.\n"
            f"{abi}include <tunables/global>\n\n"
            f"profile {PROFILE_NAME} {attachment} flags=(unconfined) {{\n  userns,\n}}\n")


def classify_launch_error(message: str, context: dict | None = None) -> str:
    """Playwright/Chromium 실행 오류 메시지를 실패 지점별 코드로 바꾼다."""
    context = context or {}
    text = message or ""
    if "Executable doesn't exist" in text or "executable doesn't exist" in text:
        return "CHROMIUM_MISSING"
    if "missing dependencies" in text or "error while loading shared libraries" in text:
        return "CHROMIUM_DEPENDENCIES_MISSING"
    if "Missing X server" in text or "cannot open display" in text or "Unable to open X display" in text:
        return "DISPLAY_MISSING"
    if "ProcessSingleton" in text or "profile appears to be in use" in text or "SingletonLock" in text:
        return "PROFILE_LOCKED"
    if "sandbox" in text.lower():
        if "Running as root" in text or context.get("is_root"):
            return "SANDBOX_ROOT_USER"
        helper = context.get("helper") or {}
        if helper.get("exists") and not helper.get("installed"):
            return "SANDBOX_HELPER_INVALID"
        apparmor = context.get("apparmor") or {}
        userns = context.get("userns") or {}
        restricted = apparmor.get("restrict_unprivileged_userns") == 1 or userns.get("unprivileged_allowed") is False
        if restricted:
            if apparmor.get("profile_installed") and apparmor.get("profile_covers_current") is False:
                return "SANDBOX_PROFILE_STALE"
            if apparmor.get("profile_installed") and apparmor.get("profile_covers_current") and apparmor.get("profile_loaded") is False:
                return "SANDBOX_PROFILE_NOT_LOADED"
            if not apparmor.get("profile_installed"):
                return "SANDBOX_USERNS_RESTRICTED"
            return "SANDBOX_PROFILE_NOT_LOADED" if apparmor.get("profile_loaded") is not True else "SANDBOX_LAUNCH_FAILED"
        return "SANDBOX_LAUNCH_FAILED"
    return "BROWSER_LAUNCH_FAILED"


def significant_line(message: str) -> str:
    """Playwright 로그에서 원인을 설명하는 줄(FATAL/sandbox/error)을 고른다. 없으면 첫 줄이다."""
    lines = [line.strip() for line in (message or "").splitlines() if line.strip()]
    for keyword in ("FATAL", "sandbox", "missing dependencies", "Executable doesn't exist", "error"):
        for line in lines:
            if keyword.lower() in line.lower():
                return line[:500]
    return lines[0][:500] if lines else message


def launch_context(executable: str | None) -> dict:
    """실행 오류 분류에 필요한 환경 사실(root 여부, AppArmor, userns, 도우미)을 모은다."""
    layout = chromium_layout(executable) if executable else {"browsers_root": None, "chromium": None}
    return {"is_root": is_root(), "apparmor": apparmor_status(layout["browsers_root"], layout.get("chromium")),
            "userns": userns_status(), "helper": shared_helper_status()}


def launch_error(message: str, context: dict | None = None) -> BrowserError:
    """실행 실패를 코드·안내가 포함된 BrowserError로 만든다."""
    code = classify_launch_error(message, context)
    return BrowserError(code, significant_line(message), fix=DOCTOR_CODES.get(code), log=message[-4000:])


def install_apparmor(browsers_root: str | None) -> dict:
    """정책 파일을 쓰고 커널에 적재한다. root가 아니면 안내와 정책 본문만 돌려준다."""
    profile = render_profile(browsers_root)
    if not sys.platform.startswith("linux"):
        return {"status": "not_applicable", "reason": "AppArmor는 Linux 전용입니다."}
    status = apparmor_status(browsers_root)
    if not status.get("enabled"):
        return {"status": "not_needed", "reason": "AppArmor가 활성화되어 있지 않습니다.", "apparmor": status}
    if status.get("restrict_unprivileged_userns") is None:
        return {"status": "not_needed", "reason": "이 커널은 비특권 user namespace를 AppArmor로 제한하지 않습니다.", "apparmor": status}
    if not is_root():
        return {"status": "needs_root", "method": "apparmor", "profile": profile, "profile_path": str(PROFILE_PATH),
                "instructions": f"sudo {Path(sys.argv[0]).name} --install-sandbox --sandbox-method apparmor 로 다시 실행하거나, 아래 profile 내용을 {PROFILE_PATH}에 저장한 뒤 sudo apparmor_parser -r {PROFILE_PATH} 를 실행하세요."}
    if not status.get("parser"):
        return {"status": "failed", "reason": "apparmor_parser를 찾지 못했습니다. apparmor 패키지를 설치하세요."}
    PROFILE_PATH.write_text(profile, encoding="utf-8")
    PROFILE_PATH.chmod(0o644)
    result = subprocess.run([status["parser"], "-r", str(PROFILE_PATH)], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return {"status": "failed", "reason": result.stderr.strip() or result.stdout.strip(), "profile_path": str(PROFILE_PATH)}
    return {"status": "installed", "method": "apparmor", "profile_path": str(PROFILE_PATH), "browsers_root": browsers_root,
            "roots": profile_roots(profile), "apparmor": apparmor_status(browsers_root)}


def install_suid(directory: str) -> dict:
    """v0.1.0 방식: Chromium 디렉터리 안에 setuid 도우미를 설치한다(설치 디렉터리를 따라다닌다)."""
    if not sys.platform.startswith("linux"):
        return {"status": "not_applicable", "reason": "setuid 샌드박스는 Linux 전용입니다."}
    source, helper = Path(directory) / "chrome_sandbox", Path(directory) / "chrome-sandbox"
    if not source.exists():
        return {"status": "failed", "reason": "chrome_sandbox 도우미가 Chromium 디렉터리에 없습니다.", "directory": directory}
    if not is_root():
        return {"status": "needs_root", "method": "suid", "instructions": f"sudo cp {source} {helper} && sudo chown root:root {helper} && sudo chmod 4755 {helper}"}
    shutil.copy2(source, helper)
    os.chown(helper, 0, 0)
    helper.chmod(0o4755)
    return {"status": "installed", "method": "suid", "path": str(helper), "suid": suid_status(directory)}


def uninstall_apparmor() -> dict:
    """정책 파일을 제거하고 커널에서 내린다. 오래된 정책 충돌을 정리할 때 쓴다."""
    if not PROFILE_PATH.exists():
        return {"status": "not_installed"}
    if not is_root():
        return {"status": "needs_root", "instructions": f"sudo apparmor_parser -R {PROFILE_PATH} && sudo rm {PROFILE_PATH}"}
    parser = apparmor_status(None).get("parser")
    if parser:
        subprocess.run([parser, "-R", str(PROFILE_PATH)], capture_output=True, text=True, timeout=30)
    PROFILE_PATH.unlink()
    return {"status": "removed", "profile_path": str(PROFILE_PATH)}


def seccomp_evidence(profile_dir: Path) -> dict | None:
    """Linux에서 렌더러 프로세스의 seccomp/NoNewPrivs 상태를 읽어 샌드박스 활성 근거를 만든다."""
    if not sys.platform.startswith("linux"):
        return None
    from .processes import chromium_processes

    renderers = [p for p in chromium_processes(profile_dir) if p["type"] in {"renderer", "utility", "gpu-process"}]
    evidence = []
    for process in renderers[:6]:
        try:
            status = Path(f"/proc/{process['pid']}/status").read_text()
        except OSError:
            continue
        fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
        evidence.append({"pid": process["pid"], "type": process["type"], "seccomp": fields.get("Seccomp", "").strip(),
                         "no_new_privs": fields.get("NoNewPrivs", "").strip()})
    return {"processes": evidence, "seccomp_filtered": any(e["seccomp"] == "2" for e in evidence)} if evidence else None


async def launch_test(playwright, config, executable: str | None, profile_dir: Path, context: dict) -> dict:
    """임시 프로필로 실제 Chromium을 샌드박스와 함께 실행해 성공/실패 코드를 판정한다."""
    env = {**os.environ, **launch_env(executable)}
    try:
        browser_context = await playwright.chromium.launch_persistent_context(
            str(profile_dir), headless=config.headless, executable_path=config.executable_path,
            chromium_sandbox=not config.no_sandbox, env=env, args=["--autoplay-policy=user-gesture-required"], timeout=30000)
    except Exception as error:  # Playwright Error/TimeoutError 모두 메시지로 분류한다.
        code = classify_launch_error(str(error), context)
        return {"ok": False, "code": code, "message": significant_line(str(error)), "fix": DOCTOR_CODES.get(code),
                "no_sandbox_flag": config.no_sandbox}
    try:
        page = browser_context.pages[0] if browser_context.pages else await browser_context.new_page()
        await page.goto("data:text/html,<title>doctor</title><main><button>ok</button></main>", wait_until="domcontentloaded")
        user_agent = await page.evaluate("navigator.userAgent")
        evidence = seccomp_evidence(profile_dir)
    finally:
        await browser_context.close()
    return {"ok": True, "sandbox_active": not config.no_sandbox, "no_sandbox_flag": config.no_sandbox,
            "evidence": "Chromium은 사용 가능한 샌드박스가 없으면 --no-sandbox 없이 시작하지 못하므로 정상 시작은 샌드박스 활성을 뜻합니다." if not config.no_sandbox else "--no-sandbox로 실행되어 샌드박스가 비활성입니다.",
            "seccomp": evidence, "user_agent": user_agent, "sandbox_source": "setuid_helper" if env.get("CHROME_DEVEL_SANDBOX") else "namespace_or_profile"}


async def doctor(config, launch: bool = True) -> dict:
    """버전·경로·사용자·디스플레이·AppArmor·userns·도우미·시스템 브라우저·샌드박스·프로세스·잠금을 단계별로 진단한다."""
    from playwright.async_api import async_playwright

    from .processes import chromium_processes, lock_status, profile_dirs

    checks = []
    # --no-sandbox를 명시한 세션에서는 샌드박스 관련 실패를 경고로 낮춘다. 자동 완화는 아니며
    # 보고서에는 제한 사실과 코드가 그대로 남는다.
    sandbox_required = not config.no_sandbox

    def check(identifier: str, status: str, **details):
        if status == "fail" and not sandbox_required and str(details.get("code", "")).startswith("SANDBOX_"):
            status = "warn"
            details["message"] = f"(--no-sandbox로 실행 중) {details.get('message', '')}"
        checks.append({"id": identifier, "status": status, **details})

    report = {"aribrowser": __version__, "platform": {"system": platform.system(), "release": platform.release(),
              "machine": platform.machine(), "python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False))}}
    try:
        user_name = getpass.getuser()
    except Exception:
        user_name = None
    report["user"] = {"name": user_name, "uid": os.geteuid() if hasattr(os, "geteuid") else None, "is_root": is_root()}
    check("user", "fail" if report["user"]["is_root"] and sys.platform.startswith("linux") else "ok",
          code="SANDBOX_ROOT_USER" if report["user"]["is_root"] and sys.platform.startswith("linux") else None,
          message=DOCTOR_CODES["SANDBOX_ROOT_USER"] if report["user"]["is_root"] and sys.platform.startswith("linux") else "일반 사용자 실행")
    xauthority = os.environ.get("XAUTHORITY")
    report["display"] = {"DISPLAY": os.environ.get("DISPLAY"), "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY"),
                         "XAUTHORITY": xauthority, "xauthority_exists": bool(xauthority) and Path(xauthority).exists()}
    display_ok = bool(report["display"]["DISPLAY"] or report["display"]["WAYLAND_DISPLAY"]) or not sys.platform.startswith("linux")
    check("display", "ok" if display_ok or config.headless else "fail", code=None if display_ok or config.headless else "DISPLAY_MISSING",
          message="headless 실행에는 디스플레이가 필요하지 않습니다." if config.headless else ("디스플레이 사용 가능" if display_ok else DOCTOR_CODES["DISPLAY_MISSING"]))
    async with async_playwright() as playwright:
        try:
            executable = config.executable_path or playwright.chromium.executable_path
        except Exception as error:
            executable = None
            check("chromium", "fail", code="CHROMIUM_MISSING", message=str(error)[:300])
        layout = chromium_layout(executable) if executable else {"chromium": None, "exists": False, "directory": None, "browsers_root": None, "headless_shell": None, "bundled": False}
        report["chromium"] = {**layout, "version": chromium_version(layout["chromium"]),
                              "headless_shell_version": chromium_version(layout["headless_shell"]),
                              "launch_path": layout["headless_shell"] if config.headless and layout["headless_shell"] and not config.executable_path else layout["chromium"]}
        if executable:
            check("chromium", "ok" if layout["exists"] else "fail", code=None if layout["exists"] else "CHROMIUM_MISSING",
                  path=layout["chromium"], version=report["chromium"]["version"])
        report["system_browsers"] = system_browsers()
        report["browser_policy"] = getattr(config, "browser", "auto")
        report["apparmor"] = apparmor_status(layout["browsers_root"], layout["chromium"])
        report["userns"] = userns_status()
        report["sandbox_helper"] = shared_helper_status()
        report["suid_sandbox"] = suid_status(layout["directory"])
        report["launch_env"] = launch_env(executable)
        apparmor = report["apparmor"]
        helper = report["sandbox_helper"]
        # 번들 Chromium이 아닌 실행 파일(시스템 브라우저, --chromium 경로)은 자체 샌드박스 구성을 가지므로
        # AriBrowser 정책/도우미의 정적 판정을 적용하지 않는다.
        foreign = bool(executable) and not layout.get("bundled") and "ms-playwright" not in str(layout.get("browsers_root") or "")
        if sys.platform.startswith("linux"):
            if helper.get("exists") and not helper.get("installed"):
                check("sandbox_helper", "fail", code="SANDBOX_HELPER_INVALID", message=DOCTOR_CODES["SANDBOX_HELPER_INVALID"], **{k: helper.get(k) for k in ("path", "owner_uid", "mode")})
            elif helper.get("installed"):
                check("sandbox_helper", "ok", message="공용 setuid 샌드박스 도우미가 설치되어 어떤 설치 경로의 Chromium도 샌드박스로 실행합니다.", path=helper["path"])
            else:
                check("sandbox_helper", "info", message="공용 도우미가 없습니다. user namespace가 허용되면 필요하지 않습니다.", path=helper["path"])
        helper_ok = helper.get("installed") or report["suid_sandbox"].get("installed")
        restricted = apparmor.get("restrict_unprivileged_userns") == 1 or report["userns"].get("unprivileged_allowed") is False
        if apparmor.get("applicable") and foreign:
            check("apparmor", "info", message="번들 Chromium이 아닌 실행 파일입니다. 해당 브라우저의 자체 샌드박스 구성을 사용하며 실제 실행 검사로 판정합니다.", path=layout["chromium"])
        elif apparmor.get("applicable"):
            if restricted and not helper_ok:
                if not apparmor["profile_installed"]:
                    check("apparmor", "fail", code="SANDBOX_USERNS_RESTRICTED", message=DOCTOR_CODES["SANDBOX_USERNS_RESTRICTED"],
                          system_browsers=[b["kind"] for b in report["system_browsers"]])
                elif apparmor["profile_covers_current"] is False:
                    check("apparmor", "fail", code="SANDBOX_PROFILE_STALE", message=DOCTOR_CODES["SANDBOX_PROFILE_STALE"],
                          profile_roots=apparmor["profile_roots"], current=layout["chromium"])
                elif apparmor["profile_loaded"] is False:
                    check("apparmor", "fail", code="SANDBOX_PROFILE_NOT_LOADED", message=DOCTOR_CODES["SANDBOX_PROFILE_NOT_LOADED"])
                else:
                    check("apparmor", "ok", message="AriBrowser 전용 AppArmor 정책이 현재 Chromium 경로를 포함합니다.", profile_roots=apparmor["profile_roots"])
            elif restricted:
                check("apparmor", "ok", message="setuid 샌드박스 도우미가 설치되어 AppArmor 정책이 필요하지 않습니다.")
            else:
                check("apparmor", "ok", message="비특권 user namespace가 제한되지 않아 추가 정책이 필요하지 않습니다.")
            check("userns", "ok" if report["userns"].get("unprivileged_allowed") or apparmor.get("profile_installed") or helper_ok else "warn",
                  unprivileged_allowed=report["userns"].get("unprivileged_allowed"))
        launch_result = None
        if launch and executable:
            root = Path(config.data_dir)
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="doctor-", dir=root) as temporary:
                launch_result = await launch_test(playwright, config, executable, Path(temporary),
                                                  {"is_root": report["user"]["is_root"], "apparmor": apparmor, "userns": report["userns"], "helper": helper})
            # --browser auto는 번들 실행이 샌드박스 문제로 실패하면 시스템 브라우저로 대체하므로 진단도 같은 순서로 검사한다.
            fallback = None
            if not launch_result["ok"] and str(launch_result.get("code", "")).startswith("SANDBOX_") and report["system_browsers"] \
                    and getattr(config, "browser", "auto") == "auto" and not config.executable_path:
                candidate = report["system_browsers"][0]
                with tempfile.TemporaryDirectory(prefix="doctor-", dir=root) as temporary:
                    import copy as _copy

                    alternative = _copy.copy(config)
                    alternative.executable_path = candidate["path"]
                    fallback = await launch_test(playwright, alternative, candidate["path"], Path(temporary),
                                                 {"is_root": report["user"]["is_root"]})
                fallback["browser"] = candidate
                launch_result = {**launch_result, "fallback": fallback}
            report["sandbox"] = launch_result
            if launch_result["ok"]:
                check("sandbox_launch", "ok", code=None, message=launch_result.get("evidence"), sandbox_active=launch_result.get("sandbox_active"))
            elif fallback and fallback.get("ok"):
                check("sandbox_launch", "warn", code=launch_result.get("code"), sandbox_active=fallback.get("sandbox_active"),
                      message=f"번들 Chromium은 실행되지 않지만(--browser auto) 시스템 브라우저 {fallback['browser']['kind']}({fallback['browser']['path']})가 샌드박스로 실행됩니다. {launch_result.get('fix') or ''}".strip())
            else:
                check("sandbox_launch", "fail", code=launch_result.get("code"), message=launch_result.get("message") or launch_result.get("evidence"),
                      sandbox_active=launch_result.get("sandbox_active"))
            # 실제 실행이 성공했으면(시스템 브라우저·도우미 등) 정적 AppArmor 판정의 실패는 참고 사항으로 낮춘다.
            if launch_result["ok"] or (fallback and fallback.get("ok")):
                for item in checks:
                    if item["id"] in {"apparmor", "userns"} and item["status"] == "fail":
                        item["status"] = "info"
                        item["message"] = f"(실제 실행은 성공) {item.get('message', '')}"
        profiles = []
        for profile in profile_dirs(config.data_dir):
            status = lock_status(profile)
            profiles.append({"profile": profile.name, "lock": status})
        stale_processes = chromium_processes(Path(config.data_dir) / "profiles")
        report["profiles"] = profiles
        report["processes"] = stale_processes
        if stale_processes:
            check("processes", "warn", message="이 data-dir를 사용하는 Chromium 프로세스가 이미 실행 중입니다. 다른 세션이거나 잔여 프로세스입니다.",
                  count=len(stale_processes))
        if any(p["lock"]["present"] and p["lock"]["stale"] for p in profiles):
            check("profile_locks", "warn", message="잔여 프로필 잠금이 있습니다. --cleanup으로 제거할 수 있습니다.",
                  profiles=[p["profile"] for p in profiles if p["lock"]["present"] and p["lock"]["stale"]])
    failures = [c for c in checks if c["status"] == "fail"]
    report["checks"] = checks
    report["summary"] = {"ok": not failures, "error_code": failures[0].get("code") if failures else None,
                         "fix": DOCTOR_CODES.get(failures[0].get("code")) if failures and failures[0].get("code") else None,
                         "warnings": [c["id"] for c in checks if c["status"] == "warn"],
                         "system_browser_fallback": bool(report.get("system_browsers")) and getattr(config, "browser", "auto") == "auto"}
    return report
