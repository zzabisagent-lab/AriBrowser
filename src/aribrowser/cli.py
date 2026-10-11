"""터미널/Agent용 JSONL 실행 파일의 명령행 옵션과 UTF-8 입출력을 정의한다."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import __version__
from .browser import Browser
from .engine import Config
from .protocol import Protocol


def parser() -> argparse.ArgumentParser:
    """GUI 없이도 즉시 사용 가능한 CLI 옵션과 일회성 화면 출력 옵션을 제공한다."""
    result = argparse.ArgumentParser(description="AriBrowser: AI Agent 전용 Chromium 텍스트 브라우저")
    result.add_argument("--version", action="version", version=f"AriBrowser {__version__}")
    result.add_argument("--data-dir", type=Path, default=Path.home() / ".aribrowser")
    result.add_argument("--headed", action="store_true", help="Chromium 창을 표시합니다.")
    result.add_argument("--no-sandbox", action="store_true", help="Chromium 샌드박스가 없는 격리된 CI/컨테이너용 옵션")
    result.add_argument("--width", type=int, default=1280)
    result.add_argument("--height", type=int, default=800)
    result.add_argument("--mobile", action="store_true")
    result.add_argument("--timeout-ms", type=int, default=10000)
    result.add_argument("--media", choices=["defer", "allow"], default="defer")
    result.add_argument("--chromium", help="직접 설치한 Chromium 실행 파일 경로")
    result.add_argument("--snapshot", metavar="URL", help="URL을 읽고 전체 화면을 출력한 뒤 종료합니다.")
    result.add_argument("--format", choices=["json", "text"], default="json")
    result.add_argument("--self-test", action="store_true", help="번들 Chromium과 DOM/접근성 연결을 검증합니다.")
    return result


def encode(value: dict) -> str:
    """항상 한 줄의 UTF-8 JSON을 출력하고 비표준 NaN 값을 허용하지 않는다."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def reject_constant(value: str):
    """JSON 표준 밖의 NaN/Infinity가 좌표나 상태에 침투하는 것을 막는다."""
    raise ValueError(f"지원하지 않는 JSON 상수: {value}")


async def run(args) -> int:
    """단일 요청 또는 지속 JSONL 세션을 실행하고 EOF/종료 시 프로필을 정상 저장한다."""
    browser = Browser(Config(data_dir=args.data_dir, headless=not args.headed, no_sandbox=args.no_sandbox,
                             width=args.width, height=args.height, mobile=args.mobile,
                             timeout_ms=args.timeout_ms, media_policy=args.media, executable_path=args.chromium))
    protocol = Protocol(browser)
    try:
        if args.snapshot or args.self_test:
            url = args.snapshot or "data:text/html,<title>AriBrowser Self Test</title><main><h1>Ready</h1><button>Test</button></main>"
            response = await protocol.handle({"id": 1, "command": "navigate", "params": {"url": url, "mode": "full"}})
            if args.self_test and response["ok"]:
                elements = response["result"]["elements"]
                if not any(e["role"] == "button" and e["accessibility_source"] == "chromium-ax" for e in elements):
                    print(encode({"ok": False, "error": {"code": "SELF_TEST_FAILED"}}), flush=True)
                    return 1
                print(encode({"ok": True, "version": __version__, "engine": "Chromium", "self_test": "passed"}), flush=True)
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


def main() -> None:
    """Windows를 포함해 UTF-8을 강제하고 Ctrl+C/BrokenPipe를 조용히 정리한다."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    if not 1 <= args.timeout_ms <= 30000:
        parser().error("--timeout-ms는 1~30000입니다.")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except BrokenPipeError:
        raise SystemExit(0) from None
