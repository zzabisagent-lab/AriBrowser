"""터미널/Agent용 JSONL 실행 파일의 명령행 옵션과 UTF-8 입출력을 정의한다.

v0.1.0 추가 옵션:
- `--doctor`: 버전·Chromium 경로·사용자·디스플레이·AppArmor·user namespace·샌드박스 실행·
  잔여 프로세스/잠금을 단계별 오류 코드와 함께 JSON으로 출력한다.
- `--install-sandbox [--sandbox-method apparmor|suid]`: Linux에서 번들 Chromium의
  샌드박스 정책을 설치하고 실제 실행으로 검증한다(root 필요, 아니면 안내 출력).
- `--uninstall-sandbox`: AppArmor 정책을 제거한다.
- `--cleanup`: 이전 세션이 남긴 Chromium 프로세스와 프로필 잠금을 정리한다.
- `--max-bytes`: 관측 명령 응답의 세션 기본 바이트 상한(0이면 무제한).

v0.2.0 추가 옵션:
- `--browser auto|bundled|system`: 번들 Chromium이 샌드박스 문제로 실행되지 않으면 설치된
  Chrome/Chromium/Edge/Brave로 대체(auto, 기본). `--no-sandbox`로 대체하지 않는다.
- `--sandbox-method auto|helper|apparmor|suid`: 기본 `auto`(=helper)는 공용 setuid 도우미를
  고정 경로에 한 번 설치해 설치 경로와 무관하게 동작한다. `apparmor`는 모든 사용자 캐시와
  배포 디렉터리 구조에 붙는 정책을 설치한다. `suid`는 v0.1.0 방식(설치 디렉터리 안)이다.

v0.3.0 추가 옵션:
- `--locale`, `--timezone`: 프로필 생성 시 명시하지 않았을 때 쓰는 세션 기본 로캘(BCP 47)과 시간대(IANA).
  `profile.create`의 `locale`/`timezone_id`가 우선하며, 프로필에 저장된 값이 그다음이다.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, processes, sandbox
from .browser import Browser
from .engine import Config, validate_locale, validate_timezone
from .errors import BrowserError
from .protocol import Protocol


def parser() -> argparse.ArgumentParser:
    """GUI 없이도 즉시 사용 가능한 CLI 옵션과 일회성 화면 출력/진단 옵션을 제공한다."""
    result = argparse.ArgumentParser(description="AriBrowser: AI Agent 전용 Chromium 텍스트 브라우저")
    result.add_argument("--version", action="version", version=f"AriBrowser {__version__}")
    result.add_argument("--data-dir", type=Path, default=Path.home() / ".aribrowser")
    result.add_argument("--headed", action="store_true", help="Chromium 창을 표시합니다.")
    result.add_argument("--no-sandbox", action="store_true", help="Chromium 샌드박스가 없는 격리된 CI/컨테이너용 옵션. 자동으로 켜지지 않습니다.")
    result.add_argument("--width", type=int, default=1280)
    result.add_argument("--height", type=int, default=800)
    result.add_argument("--mobile", action="store_true")
    result.add_argument("--timeout-ms", type=int, default=10000)
    result.add_argument("--media", choices=["defer", "allow"], default="defer")
    result.add_argument("--chromium", help="직접 설치한 Chromium 실행 파일 경로")
    result.add_argument("--browser", choices=["auto", "bundled", "system"], default="auto",
                        help="auto: 번들 Chromium, 샌드박스 불가 시 시스템 Chrome/Chromium/Edge/Brave로 대체. bundled/system: 해당 브라우저만.")
    result.add_argument("--max-bytes", type=int, default=500_000, help="관측 응답의 기본 바이트 상한(0=무제한, 요청별 max_bytes로 재지정)")
    result.add_argument("--locale", help="프로필 기본 로캘(BCP 47, 예: en-US). Accept-Language와 navigator.language에 적용됩니다.")
    result.add_argument("--timezone", help="프로필 기본 시간대(IANA, 예: UTC, Asia/Seoul).")
    result.add_argument("--snapshot", metavar="URL", help="URL을 읽고 전체 화면을 출력한 뒤 종료합니다.")
    result.add_argument("--format", choices=["json", "text"], default="json")
    result.add_argument("--self-test", action="store_true", help="번들 Chromium과 DOM/접근성 연결을 검증합니다.")
    result.add_argument("--doctor", action="store_true", help="실행 환경을 단계별로 진단하고 JSON으로 출력합니다.")
    result.add_argument("--no-launch", action="store_true", help="--doctor에서 실제 Chromium 실행 검사를 생략합니다.")
    result.add_argument("--install-sandbox", action="store_true", help="Linux 번들 Chromium 샌드박스 정책을 설치/갱신합니다(root).")
    result.add_argument("--uninstall-sandbox", action="store_true", help="설치한 AppArmor 정책을 제거합니다(root).")
    result.add_argument("--sandbox-method", choices=["auto", "helper", "apparmor", "suid"], default="auto",
                        help="helper(기본): 공용 setuid 도우미를 /usr/local/lib/aribrowser에 한 번 설치. apparmor: 경로 독립 AppArmor 정책. suid: 설치 디렉터리 안 도우미(0.1.0 방식).")
    result.add_argument("--cleanup", action="store_true", help="이전 세션의 잔여 Chromium 프로세스와 프로필 잠금을 정리합니다.")
    return result


def encode(value: dict) -> str:
    """항상 한 줄의 UTF-8 JSON을 출력하고 비표준 NaN 값을 허용하지 않는다."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=str)


def reject_constant(value: str):
    """JSON 표준 밖의 NaN/Infinity가 좌표나 상태에 침투하는 것을 막는다."""
    raise ValueError(f"지원하지 않는 JSON 상수: {value}")


def config_from(args) -> Config:
    """명령행 옵션을 엔진 설정으로 바꾼다."""
    return Config(data_dir=args.data_dir, headless=not args.headed, no_sandbox=args.no_sandbox,
                  width=args.width, height=args.height, mobile=args.mobile,
                  timeout_ms=args.timeout_ms, media_policy=args.media, executable_path=args.chromium,
                  max_bytes=args.max_bytes, browser=args.browser, locale=args.locale, timezone_id=args.timezone)


async def run_doctor(args) -> int:
    """진단 보고서를 출력하고 실패가 있으면 1을 돌려준다."""
    report = await sandbox.doctor(config_from(args), launch=not args.no_launch)
    print(encode(report), flush=True)
    return 0 if report["summary"]["ok"] else 1


async def resolve_chromium(config) -> tuple[str, dict]:
    """설치/진단에 쓸 Chromium 경로를 해석한다.

    `sudo`로 실행하면 HOME이 root로 바뀌어 Playwright가 /root/.cache 아래를 보게 된다.
    번들 바이너리는 설치 디렉터리 안의 Chromium을 쓰므로 영향이 없지만, 소스 실행이나
    사용자 캐시를 쓰는 경우에는 호출한 사용자(SUDO_USER)의 HOME으로 다시 해석한다.
    """
    from playwright.async_api import async_playwright

    notes = {}
    async with async_playwright() as playwright:
        executable = config.executable_path or playwright.chromium.executable_path
    sudo_user = os.environ.get("SUDO_USER")
    if not Path(executable).exists() and not config.executable_path and sudo_user and hasattr(os, "getuid"):
        import pwd

        home = pwd.getpwnam(sudo_user).pw_dir
        os.environ["HOME"] = home
        async with async_playwright() as playwright:
            executable = playwright.chromium.executable_path
        notes = {"resolved_for_user": sudo_user, "home": home}
    return executable, notes


def demoted_doctor(args, user: str) -> dict | None:
    """root로 설치한 뒤 호출 사용자 권한으로 --doctor를 실행해 샌드박스 적용을 검증한다."""
    import pwd

    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        return None
    command = [sys.executable, "--doctor"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "aribrowser", "--doctor"]
    command += ["--data-dir", str(args.data_dir), "--browser", "bundled"]
    if args.chromium:
        command += ["--chromium", args.chromium]

    def demote():
        """자식 프로세스에서 그룹/사용자 권한을 내린다."""
        os.setgid(entry.pw_gid)
        os.setuid(entry.pw_uid)

    env = {**os.environ, "HOME": entry.pw_dir, "USER": user, "LOGNAME": user}
    env.pop("SUDO_USER", None)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120, env=env, preexec_fn=demote)
        report = json.loads(completed.stdout) if completed.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    if not report:
        return None
    return {"ok": report["summary"]["ok"], "error_code": report["summary"]["error_code"], "sandbox": report.get("sandbox"),
            "verified_as": user}


async def run_install_sandbox(args) -> int:
    """정책을 설치한 뒤 실제 샌드박스 실행으로 검증한다. root가 아니면 안내만 출력하고 2를 돌려준다."""
    config = config_from(args)
    executable, notes = await resolve_chromium(config)
    layout = sandbox.chromium_layout(executable)
    if not layout["exists"]:
        print(encode({"install": {"status": "failed", "code": "CHROMIUM_MISSING", "reason": sandbox.DOCTOR_CODES["CHROMIUM_MISSING"]},
                      "chromium": layout}), flush=True)
        return 1
    method = "helper" if args.sandbox_method == "auto" else args.sandbox_method
    if method == "helper":
        result = sandbox.install_helper(executable)
    elif method == "suid":
        result = sandbox.install_suid(layout["directory"])
    else:
        result = sandbox.install_apparmor(layout["browsers_root"])
    output = {"install": result, "chromium": layout, **notes}
    if result["status"] in {"installed", "not_needed"}:
        # 설치 직후 실제 실행으로 적용 상태를 검증한다. root는 샌드박스를 쓸 수 없으므로 호출 사용자로 내려서 검증한다.
        if sandbox.is_root():
            sudo_user = os.environ.get("SUDO_USER")
            verification = demoted_doctor(args, sudo_user) if sudo_user and sudo_user != "root" else None
            output["verification"] = verification or {"skipped": True, "reason": "root는 Chromium 샌드박스를 사용할 수 없으므로 일반 사용자로 --doctor를 실행해 검증하세요."}
        else:
            config.browser = "bundled"  # 설치 검증은 번들 Chromium 자체가 샌드박스로 실행되는지 본다.
            report = await sandbox.doctor(config, launch=True)
            output["verification"] = {"ok": report["summary"]["ok"], "error_code": report["summary"]["error_code"],
                                      "sandbox": report.get("sandbox")}
    print(encode(output), flush=True)
    if output.get("verification", {}).get("ok") is False:
        return 1
    return {"installed": 0, "not_needed": 0, "needs_root": 2, "not_applicable": 0}.get(result["status"], 1)


async def run(args) -> int:
    """단일 요청 또는 지속 JSONL 세션을 실행하고 EOF/종료 시 프로필을 정상 저장한다."""
    browser = Browser(config_from(args))
    protocol = Protocol(browser)
    try:
        if args.snapshot or args.self_test:
            url = args.snapshot or "data:text/html,<title>AriBrowser Self Test</title><main><h1>Ready</h1><button>Test</button></main>"
            response = await protocol.handle({"id": 1, "command": "navigate", "params": {"url": url, "mode": "full", "format": "both", "compact": False, "max_bytes": 0}})
            if args.self_test and response["ok"]:
                elements = response["result"]["elements"]
                if not any(e["role"] == "button" and e["accessibility_source"] == "chromium-ax" for e in elements):
                    print(encode({"ok": False, "error": {"code": "SELF_TEST_FAILED"}}), flush=True)
                    return 1
                print(encode({"ok": True, "version": __version__, "engine": "Chromium", "self_test": "passed",
                              "sandbox": not args.no_sandbox}), flush=True)
            elif args.self_test:
                print(encode({"ok": False, "error": response["error"], "hint": "./aribrowser --doctor 로 원인을 확인하세요."}), flush=True)
            else:
                print(response["result"]["text"] if args.format == "text" and response["ok"] else encode(response), flush=True)
            return 0 if response["ok"] else 1
        while not protocol.closed:
            line = await asyncio.to_thread(sys.stdin.readline, 1024 * 1024 + 1)
            if not line:
                break
            if not line.strip():
                continue
            if len(line) > 1024 * 1024:
                while line and not line.endswith("\n"):
                    line = await asyncio.to_thread(sys.stdin.readline, 1024 * 1024 + 1)
                response = {"id": None, "ok": False, "error": {"code": "REQUEST_TOO_LARGE", "message": "요청 한도는 1 MiB 문자입니다."}}
            else:
                try:
                    request = json.loads(line, parse_constant=reject_constant)
                    response = await protocol.handle(request)
                except (json.JSONDecodeError, ValueError) as error:
                    response = {"id": None, "ok": False, "error": {"code": "INVALID_JSON", "message": str(error)}}
            print(encode(response), flush=True)
        return 0
    finally:
        await browser.close()


def prefer_bundled_browsers() -> None:
    """번들 바이너리는 호스트의 PLAYWRIGHT_BROWSERS_PATH와 무관하게 자신에게 포함된 Chromium을 쓴다(v0.3.0).

    playwright-python은 PyInstaller 번들에서 PLAYWRIGHT_BROWSERS_PATH가 비어 있을 때만 "0"(패키지 안의
    .local-browsers)을 쓴다. GitHub Actions 러너처럼 그 변수가 사용자 캐시를 가리키는 환경에서는 번들
    Chromium 대신 캐시의 다른 리비전이 실행되어 `runtime.resolved_executable`이 설치 경로를 벗어났다.
    다른 실행 파일을 쓰려면 `--chromium <경로>`를 지정한다.
    """
    local = sandbox.bundle_browsers_dir()
    if local is not None:
        # "0"(패키지 상대) 대신 절대 경로를 준다. 드라이버가 어떤 작업 디렉터리/패키지 루트를 쓰든 같은 디렉터리다.
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local)


def main() -> None:
    """Windows를 포함해 UTF-8을 강제하고 Ctrl+C/BrokenPipe를 조용히 정리한다."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    prefer_bundled_browsers()
    args = parser().parse_args()
    if not 1 <= args.timeout_ms <= 30000:
        parser().error("--timeout-ms는 1~30000입니다.")
    if args.max_bytes < 0:
        parser().error("--max-bytes는 0 이상입니다.")
    try:
        args.locale = validate_locale(args.locale)
        args.timezone = validate_timezone(args.timezone)
    except BrowserError as error:
        parser().error(f"{error} ({error.details})")
    try:
        if args.cleanup:
            print(encode(processes.cleanup(args.data_dir)), flush=True)
            raise SystemExit(0)
        if args.uninstall_sandbox:
            result = {"helper": sandbox.uninstall_helper(), "apparmor": sandbox.uninstall_apparmor()}
            print(encode(result), flush=True)
            raise SystemExit(2 if any(r["status"] == "needs_root" for r in result.values()) else 0)
        if args.install_sandbox:
            raise SystemExit(asyncio.run(run_install_sandbox(args)))
        if args.doctor:
            raise SystemExit(asyncio.run(run_doctor(args)))
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except BrokenPipeError:
        raise SystemExit(0) from None
