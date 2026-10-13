"""Linux 배포 디렉터리(dist/aribrowser)를 Debian 패키지(.deb)로 묶는다(v0.3.0).

tarball은 압축을 풀기만 하면 되지만 Ubuntu 24.04처럼 비특권 user namespace가 제한된 호스트에서는
번들 Chromium의 샌드박스가 동작하지 않아 `sudo aribrowser --install-sandbox`를 한 번 더 실행해야
한다. .deb는 그 단계를 패키지 수명주기에 연결한다.

- 설치(postinst): 공용 setuid 샌드박스 도우미를 /usr/local/lib/aribrowser/chrome-sandbox에 설치한다.
  (Google Chrome .deb가 쓰는 것과 같은 방식. 전역 AppArmor/sysctl 설정은 바꾸지 않는다.)
- 업그레이드: 설치 경로(/opt/aribrowser)가 바뀌지 않고 도우미는 경로·리비전과 무관하므로 설정이 유지된다.
- 제거(prerm, 업그레이드가 아닌 remove/purge만): AriBrowser가 설치한 도우미와 AppArmor 정책만 제거한다.
- 어떤 단계에서도 `--no-sandbox`를 켜지 않는다.

패키지 내용: /opt/aribrowser(PyInstaller 번들 전체), /usr/bin/aribrowser(심볼릭 링크),
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

POSTINST = """#!/bin/sh
# AriBrowser 설치 후: 공용 setuid 샌드박스 도우미를 호스트당 한 번 설치한다(경로·리비전·사용자 무관).
set -e
if [ "$1" = "configure" ]; then
  if out=$(/opt/aribrowser/aribrowser --install-sandbox --data-dir /var/lib/aribrowser 2>/dev/null); then
    echo "aribrowser: sandbox helper installed at /usr/local/lib/aribrowser/chrome-sandbox"
  else
    echo "aribrowser: sandbox helper installation did not complete; run 'sudo aribrowser --install-sandbox' and 'aribrowser --doctor'" >&2
  fi
fi
exit 0
"""

PRERM = """#!/bin/sh
# AriBrowser 제거 전: 업그레이드가 아닌 remove/purge에서만 AriBrowser가 설치한 도우미와 정책을 제거한다.
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  /opt/aribrowser/aribrowser --uninstall-sandbox >/dev/null 2>&1 || true
fi
exit 0
"""


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
            " protocol on stdin/stdout. This package installs the shared setuid sandbox\n"
            " helper once per host so the bundled Chromium runs sandboxed on Ubuntu 24.04\n"
            " without relaxing global security settings.\n", encoding="utf-8")
        for script_name, body in (("postinst", POSTINST), ("prerm", PRERM)):
            script = control_dir / script_name
            script.write_text(body, encoding="utf-8")
            script.chmod(0o755)
        (control_dir / "control").chmod(0o644)
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
