"""Chromium 프로세스와 프로필 잠금을 추적·정리한다.

브라우저를 닫은 뒤 자식 프로세스나 프로필 잠금이 남으면 다음 실행이 충돌한다.
Playwright는 브라우저 PID를 노출하지 않으므로 psutil로 `--user-data-dir=<프로필>`
인자를 가진 프로세스를 찾아 세션이 만든 Chromium을 식별한다. 같은 방식으로
이전 세션이 남긴 잔여 프로세스도 찾을 수 있다.

프로필 잠금(Linux/macOS: SingletonLock 심볼릭 링크 `hostname-pid`, Windows: lockfile)은
소유 프로세스가 살아 있는지로 유효/잔여를 구분한다.
"""

import os
import socket
import sys
import time
from pathlib import Path

import psutil


def _profile_of(cmdline: list[str]) -> str | None:
    """명령행에서 Chromium 사용자 데이터 디렉터리를 읽는다."""
    for argument in cmdline:
        if argument.startswith("--user-data-dir="):
            return argument.split("=", 1)[1]
    return None


def _describe(process: psutil.Process, profile_path: Path) -> dict:
    """프로세스 하나를 보고용 사전으로 바꾼다. 종류는 Chromium의 --type 인자에서 읽는다."""
    with process.oneshot():
        cmdline = process.cmdline() or []
        kind = next((a.split("=", 1)[1] for a in cmdline if a.startswith("--type=")), "browser")
        return {"pid": process.pid, "name": process.name(), "type": kind, "profile": str(profile_path),
                "parent": process.ppid(), "age_s": int(time.time() - process.create_time())}


def chromium_processes(root: Path) -> list[dict]:
    """root(프로필 디렉터리 또는 data_dir) 아래 프로필을 쓰는 Chromium 프로세스 트리를 나열한다.

    렌더러/GPU/유틸리티 자식 프로세스는 명령행에 --user-data-dir을 갖지 않으므로
    브라우저 주 프로세스를 먼저 찾고 그 자손을 함께 포함한다.
    """
    root = Path(root).resolve()
    found: dict[int, dict] = {}
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            profile = _profile_of(process.info["cmdline"] or [])
            if not profile:
                continue
            profile_path = Path(profile).resolve()
            if profile_path != root and root not in profile_path.parents:
                continue
            if process.pid not in found:
                found[process.pid] = _describe(process, profile_path)
            for child in process.children(recursive=True):
                if child.pid not in found:
                    found[child.pid] = _describe(child, profile_path)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
            continue
    return sorted(found.values(), key=lambda item: item["pid"])


def terminate(pids: list[int], timeout: float = 3.0) -> dict:
    """프로세스를 정상 종료 요청 후 남으면 강제 종료한다. 처리 결과 수를 돌려준다."""
    processes = []
    for pid in pids:
        try:
            processes.append(psutil.Process(pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for process in processes:
        try:
            process.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, alive = psutil.wait_procs(processes, timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    _, still = psutil.wait_procs(alive, timeout=2)
    return {"requested": len(pids), "terminated": len(processes) - len(still), "killed": len(alive) - len(still),
            "remaining": [p.pid for p in still]}


async def wait_for_exit(root: Path, timeout: float = 3.0) -> list[dict]:
    """프로필 관련 프로세스가 스스로 끝나기를 잠시 기다린 뒤 남은 목록을 돌려준다."""
    import asyncio

    deadline = time.monotonic() + timeout
    remaining = chromium_processes(root)
    while remaining and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        remaining = chromium_processes(root)
    return remaining


def lock_status(profile_dir: Path) -> dict:
    """프로필 잠금 파일의 존재, 소유 프로세스, 잔여 여부를 판단한다."""
    profile_dir = Path(profile_dir)
    if sys.platform == "win32":
        lock = profile_dir / "lockfile"
        if not lock.exists():
            return {"path": None, "present": False, "stale": False, "holder_pid": None, "holder_alive": None}
        try:
            # Chromium은 실행 중 lockfile을 배타적으로 열어 둔다. 열리면 더 이상 사용 중이 아니다.
            with lock.open("a"):
                pass
            return {"path": str(lock), "present": True, "stale": True, "holder_pid": None, "holder_alive": False}
        except PermissionError:
            return {"path": str(lock), "present": True, "stale": False, "holder_pid": None, "holder_alive": True}
    lock = profile_dir / "SingletonLock"
    if not lock.is_symlink() and not lock.exists():
        return {"path": None, "present": False, "stale": False, "holder_pid": None, "holder_alive": None}
    target = os.readlink(lock) if lock.is_symlink() else ""
    hostname, _, pid_text = target.rpartition("-")
    pid = int(pid_text) if pid_text.isdigit() else None
    same_host = hostname == socket.gethostname()
    alive = bool(pid) and psutil.pid_exists(pid) if same_host else None
    # 다른 호스트 이름(컨테이너 재생성 등)이 남긴 잠금은 생존 여부를 알 수 없어 잔여로 본다.
    stale = (not same_host) or not alive
    return {"path": str(lock), "present": True, "stale": stale, "holder_pid": pid, "holder_alive": alive,
            "holder_host": hostname or None}


def release_stale_lock(profile_dir: Path) -> dict:
    """잔여 잠금만 제거한다. 살아 있는 프로세스의 잠금은 건드리지 않는다."""
    status = lock_status(profile_dir)
    removed = []
    if status["present"] and status["stale"]:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile"):
            path = Path(profile_dir) / name
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
                    removed.append(name)
            except OSError:
                pass
    return {**status, "removed": removed}


def profile_dirs(data_dir: Path) -> list[Path]:
    """data_dir 아래 프로필 디렉터리 목록."""
    root = Path(data_dir) / "profiles"
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def cleanup(data_dir: Path, exclude_pids: set[int] | None = None) -> dict:
    """이전 세션의 잔여 Chromium 프로세스를 종료하고 잔여 잠금을 제거한다."""
    exclude = exclude_pids or set()
    stale = [p for p in chromium_processes(Path(data_dir) / "profiles") if p["pid"] not in exclude]
    result = terminate([p["pid"] for p in stale]) if stale else {"requested": 0, "terminated": 0, "killed": 0, "remaining": []}
    locks = []
    for profile in profile_dirs(data_dir):
        status = release_stale_lock(profile)
        if status["present"]:
            locks.append({"profile": profile.name, **status})
    return {"processes": {**result, "found": stale}, "locks": locks}
