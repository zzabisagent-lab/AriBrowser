"""현재 OS의 Python·Playwright·Chromium을 함께 묶은 독립 실행 배포 파일을 만든다.

PyInstaller는 교차 컴파일러가 아니므로 각 OS의 네이티브 CI에서 실행한다.
압축 파일에는 실행 파일, 런타임 디렉터리, 사용 설명서와 라이선스를 포함한다.

패키징 뒤에는 번들 바이너리로 `--doctor`와 `--self-test`를 실행해 실제 Chromium
실행을 검증한다. 호스트가 샌드박스를 지원하지 않을 때(Ubuntu 24.04의 AppArmor
userns 제한, root 실행 등)만 `--no-sandbox`로 다시 검증하고 그 사실을 출력한다.
런타임에서는 어떤 경우에도 `--no-sandbox`를 자동으로 켜지 않는다.

v0.3.0: Linux에서 dpkg-deb가 있으면 같은 번들로 Debian 패키지(.deb)도 만든다(scripts/build_deb.py).
설치 시 샌드박스 도우미를 함께 설치해 Ubuntu 24.04에서 압축 해제만으로는 안 되던 번들 Chromium
샌드박스 실행을 패키지 설치만으로 끝낸다. tzdata 패키지의 시간대 데이터를 함께 묶어 Windows에서도
`timezone_id` 검증이 동작하게 한다.
"""

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from aribrowser import __version__


def smoke_test(binary: Path) -> None:
    """번들 바이너리의 진단과 자체 검사를 실행한다. 샌드박스 불가 호스트에서만 --no-sandbox로 재시도한다."""
    with tempfile.TemporaryDirectory(prefix="ari-binary-test-") as directory:
        # 사용자 캐시를 비워도 실행되어야 Chromium 포함 여부를 실제로 검증할 수 있다.
        env = dict(os.environ)
        env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        doctor = subprocess.run([str(binary), "--doctor", "--data-dir", directory], env=env, capture_output=True,
                                text=True, encoding="utf-8", timeout=180)
        report = json.loads(doctor.stdout) if doctor.stdout.strip() else {}
        print("doctor:", json.dumps(report.get("summary"), ensure_ascii=False), file=sys.stderr)
        if not report.get("chromium", {}).get("exists"):
            raise SystemExit(f"번들 Chromium을 찾지 못했습니다: {report.get('chromium')}")
        smoke = [str(binary), "--self-test", "--data-dir", directory]
        result = subprocess.run(smoke, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
        if result.returncode == 0:
            print("self-test (sandboxed):", result.stdout.strip(), file=sys.stderr)
            return
        code = report.get("summary", {}).get("error_code") or ""
        if sys.platform.startswith("linux") and (code.startswith("SANDBOX_") or "sandbox" in result.stdout.lower()):
            print(f"WARNING: 이 호스트는 Chromium 샌드박스를 지원하지 않습니다({code}). 번들 검증만 --no-sandbox로 재시도합니다.",
                  file=sys.stderr)
            subprocess.run(smoke + ["--no-sandbox"], env=env, check=True, timeout=120)
            return
        raise SystemExit(f"self-test 실패:\n{result.stdout}\n{result.stderr}")


def main() -> None:
    """Chromium 번들 설치→패키징→독립 실행 검증→압축 및 SHA-256 생성을 수행한다."""
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    # Chromium 앱/프레임워크를 PyInstaller가 개별 바이너리로 다시 서명하면 macOS의
    # 중첩 번들이 깨진다. 별도 위치에 설치하고 원본 디렉터리를 패키징 뒤 복사한다.
    browser_source = root / "build" / "browser-bundle"
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(browser_source))
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], env=env, check=True)
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", "--onedir", "--noupx",
        "--name", "aribrowser", "--collect-data", "aribrowser", "--collect-data", "tzdata",
        "--additional-hooks-dir", "scripts/hooks", "src/aribrowser/__main__.py",
    ], env=env, check=True)
    bundle = root / "dist" / "aribrowser"
    browser_target = bundle / "_internal" / "playwright" / "driver" / "package" / ".local-browsers"
    shutil.copytree(browser_source, browser_target, symlinks=True)
    for name in ("README.md", "CHANGELOG.md", "LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(root / name, bundle / name)
    shutil.copytree(root / "docs", bundle / "docs", dirs_exist_ok=True)
    license_dir = bundle / "licenses"
    shutil.copytree(root / "licenses", license_dir, dirs_exist_ok=True)
    # wheel 메타데이터의 원문 라이선스를 함께 배포하여 런타임 구성요소의 조건을 보존한다.
    for distribution in importlib.metadata.distributions():
        for item in distribution.files or []:
            if ".dist-info/" in str(item) and any(word in item.name.lower() for word in ("license", "copying", "notice")):
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    target_dir = license_dir / distribution.metadata["Name"]
                    target_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target_dir / source.name)
    binary = bundle / ("aribrowser.exe" if sys.platform == "win32" else "aribrowser")
    smoke_test(binary)
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, "linux")
    arch = {"AMD64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(platform.machine(), platform.machine())
    name = f"aribrowser-{__version__}-{system}-{arch}"
    artifacts = root / "artifacts"
    artifacts.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(artifacts / name), "zip" if system == "windows" else "gztar",
                                      root_dir=bundle.parent, base_dir=bundle.name))
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    archive.with_name(archive.name + ".sha256").write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="utf-8")
    print(f"Built and verified: {archive.name} ({archive.stat().st_size:,} bytes)")
    if system == "linux" and shutil.which("dpkg-deb"):
        from build_deb import build as build_deb

        build_deb(bundle, artifacts)


if __name__ == "__main__":
    main()
