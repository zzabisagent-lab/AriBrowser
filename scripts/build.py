"""현재 OS의 Python·Playwright·Chromium을 함께 묶은 독립 실행 배포 파일을 만든다.

PyInstaller는 교차 컴파일러가 아니므로 각 OS의 네이티브 CI에서 실행한다.
압축 파일에는 실행 파일, 런타임 디렉터리, 사용 설명서와 라이선스를 포함한다.
"""

import hashlib
import importlib.metadata
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from aribrowser import __version__


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
        "--name", "aribrowser", "--collect-data", "aribrowser",
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
    with tempfile.TemporaryDirectory(prefix="ari-binary-test-") as directory:
        # 사용자 캐시를 비워도 실행되어야 Chromium 포함 여부를 실제로 검증할 수 있다.
        smoke_env = dict(os.environ)
        smoke_env.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        smoke = [str(binary), "--self-test", "--data-dir", directory]
        if sys.platform.startswith("linux"):
            smoke.append("--no-sandbox")
        subprocess.run(smoke, env=smoke_env, check=True, timeout=90)
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


if __name__ == "__main__":
    main()
