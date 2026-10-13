"""Linux 배포 디렉터리(dist/aribrowser)를 Debian 패키지(.deb)로 묶는다(v0.3.0).

tarball은 압축을 풀기만 하면 되지만 Ubuntu 24.04처럼 비특권 user namespace가 제한된 호스트에서는
번들 Chromium의 샌드박스가 동작하지 않아 `sudo aribrowser --install-sandbox`를 한 번 더 실행해야
한다. .deb는 그 단계를 패키지 자체에 넣는다.

- 패키지가 setuid root `chrome-sandbox`(Chromium이 제공하는 chrome_sandbox의 복사본, 4755)를 Chromium
  디렉터리와 headless shell 디렉터리 안에 **함께 담는다**. Google Chrome .deb가 /opt/google/chrome/chrome-sandbox를
  두는 것과 같은 방식이다. Chromium은 실행 파일이 사용자 소유가 아닐 때(/opt의 패키지) 환경 변수
  CHROME_DEVEL_SANDBOX(공용 도우미)를 읽지 않고 실행 파일 옆의 chrome-sandbox만 쓴다.
- 설치/업그레이드/제거는 모두 dpkg가 처리한다. 유지보수 스크립트가 /usr/local이나 /etc를 건드리지 않으므로
  제거 시 패키지 파일만 사라지고, 설치 경로가 바뀌어도(/opt/aribrowser 고정) 샌드박스 설정이 유지된다.
- 전역 AppArmor/sysctl 설정은 바꾸지 않으며 어떤 단계에서도 `--no-sandbox`를 켜지 않는다.

패키지 내용: /opt/aribrowser(PyInstaller 번들 전체 + setuid chrome-sandbox), /usr/bin/aribrowser(심볼릭 링크),
/usr/share/doc/aribrowser(README/CHANGELOG/copyright). 의존성은 Playwright가 Ubuntu 22.04/24.04에
설치하는 Chromium 공유 라이브러리 목록을 양쪽 패키지 이름(t64 전환)으로 나열한다.

사용: python scripts/build_deb.py [--bundle dist/aribrowser] [--output artifacts]
"""

import argparse
import hashlib
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aribrowser import __version__  # noqa: E402

# Playwright의 ubuntu22.04/ubuntu24.04 Chromium 의존성. 24.04의 t64 이름을 앞에, 22.04 이름을 대안으로 둔다.
DEPENDS = [
    "libasound2t64 | libasound2", "libatk-bridge2.0-0t64 | libatk-bridge2.0-0", "libatk1.0-0t64 | libatk1.0-0",
    "libatspi2.0-0t64 | libatspi2.0-0", "libcairo2", "libcups2t64 | libcups2", "libdbus-1-3", "libdrm2", "libgbm1",
    "libglib2.0-0t64 | libglib2.0-0", "libnspr4", "libnss3", "libpango-1.0-0", "libx11-6", "libxcb1", "libxcomposite1",
    "libxdamage1", "libxext6", "libxfixes3", "libxkbcommon0", "libxrandr2", "libfontconfig1", "libfreetype6",
]
RECOMMENDS = ["fonts-liberation", "fonts-noto-color-emoji", "fonts-unifont", "fonts-ipafont-gothic", "fonts-wqy-zenhei"]

def add_sandbox_helpers(browsers: Path) -> list[Path]:
    """chrome_sandbox 원본을 찾아 Chromium 디렉터리와 headless shell 디렉터리에 `chrome-sandbox`로 복사한다."""
    sources = sorted(browsers.glob("chromium-*/*/chrome_sandbox"))
    if not sources:
        return []
    source = sources[-1]
    targets = [source.parent / "chrome-sandbox"]
    targets += [directory / "chrome-sandbox" for directory in sorted(browsers.glob("chromium_headless_shell-*/*/")) if directory.is_dir()]
    for target in targets:
        shutil.copy2(source, target)
    return targets


def debian_architecture() -> str:
    """dpkg 아키텍처 이름. x86_64 → amd64, aarch64 → arm64."""
    return {"x86_64": "amd64", "AMD64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), platform.machine())


def build(bundle: Path, output: Path) -> Path:
    """번들을 스테이징 디렉터리에 배치하고 dpkg-deb로 패키지를 만든 뒤 SHA-256을 기록한다."""
    if not (bundle / "aribrowser").is_file():
        raise SystemExit(f"번들 실행 파일이 없습니다: {bundle / 'aribrowser'}")
    if not shutil.which("dpkg-deb"):
        raise SystemExit("dpkg-deb가 없습니다(Debian/Ubuntu에서만 .deb를 만들 수 있습니다).")
    arch = debian_architecture()
    name = f"aribrowser_{__version__}_{arch}.deb"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aribrowser-deb-") as temp:
        root = Path(temp) / "root"
        opt = root / "opt" / "aribrowser"
        shutil.copytree(bundle, opt, symlinks=True)
        (root / "usr" / "bin").mkdir(parents=True)
        os.symlink("/opt/aribrowser/aribrowser", root / "usr" / "bin" / "aribrowser")
        doc = root / "usr" / "share" / "doc" / "aribrowser"
        doc.mkdir(parents=True)
        for item in ("README.md", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md"):
            if (bundle / item).is_file():
                shutil.copy2(bundle / item, doc / item)
        if (bundle / "LICENSE").is_file():
            shutil.copy2(bundle / "LICENSE", doc / "copyright")
        # setuid 샌드박스 도우미를 Chromium/headless shell 디렉터리에 넣는다(dpkg가 4755 root:root로 설치한다).
        helpers = add_sandbox_helpers(opt / "_internal" / "playwright" / "driver" / "package" / ".local-browsers")
        if not helpers:
            raise SystemExit("번들에서 chrome_sandbox를 찾지 못해 setuid 도우미를 넣을 수 없습니다.")
        control_dir = root / "DEBIAN"
        control_dir.mkdir()
        # 빌드 호스트의 umask(예: 077)와 무관하게 모든 사용자가 읽고 실행할 수 있어야 한다. dpkg-deb도 DEBIAN
        # 디렉터리가 0755~0775가 아니면 거부한다. 디렉터리는 755, 실행 파일은 755, 그 밖의 파일은 644로 맞춘다.
        for path in [root, *root.rglob("*")]:
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            if path.is_dir() or mode & stat.S_IXUSR:
                path.chmod((mode | 0o755) & ~0o022)
            else:
                path.chmod((mode | 0o644) & ~0o022)
        size_kb = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()) // 1024
        (control_dir / "control").write_text(
            f"Package: aribrowser\nVersion: {__version__}\nSection: web\nPriority: optional\nArchitecture: {arch}\n"
            f"Installed-Size: {size_kb}\nMaintainer: AriBrowser maintainers <noreply@github.com>\n"
            f"Depends: {', '.join(DEPENDS)}\nRecommends: {', '.join(RECOMMENDS)}\n"
            "Homepage: https://github.com/zzabisagent-lab/AriBrowser\n"
            "Description: Chromium-powered JSONL text browser for AI agents\n"
            " AriBrowser drives a bundled, sandboxed Chromium through a line-delimited JSON\n"
            " protocol on stdin/stdout. This package ships the setuid sandbox helper next to\n"
            " the bundled Chromium so it runs sandboxed on Ubuntu 24.04 without relaxing\n"
            " global security settings.\n", encoding="utf-8")
        (control_dir / "control").chmod(0o644)
        # 권한 정규화 뒤에 setuid 비트를 다시 건다(위 chmod가 4755의 setuid 비트를 지우지 않도록 마지막에 처리).
        for helper in helpers:
            helper.chmod(0o4755)
        target = output / name
        if target.exists():
            target.unlink()
        # gzip 압축: xz보다 훨씬 빠르고 tarball(gztar)과 같은 수준이다. 소유자는 root로 기록한다.
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zgzip", "--build", str(root), str(target)], check=True)
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    target.with_name(target.name + ".sha256").write_text(f"{digest.hexdigest()}  {target.name}\n", encoding="utf-8")
    print(f"Built Debian package: {target.name} ({target.stat().st_size:,} bytes)")
    return target


def main() -> None:
    """명령행에서 번들/출력 경로를 받아 패키지를 만든다."""
    parser = argparse.ArgumentParser(description="AriBrowser .deb 패키지 생성")
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--bundle", type=Path, default=root / "dist" / "aribrowser")
    parser.add_argument("--output", type=Path, default=root / "artifacts")
    args = parser.parse_args()
    build(args.bundle, args.output)


if __name__ == "__main__":
    main()
