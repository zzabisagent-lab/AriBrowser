# Linux Chromium 샌드박스 설치와 진단

AriBrowser는 Chromium의 보안 격리(샌드박스)를 켠 채 실행하며 어떤 실패 경로에서도 `--no-sandbox`를 자동으로 켜지 않습니다. Ubuntu 23.10/24.04 이상은 AppArmor로 비특권 user namespace를 제한하므로(`kernel.apparmor_restrict_unprivileged_userns=1`) 정책 없이 실행하면 다음 오류가 납니다.

```text
Chromium sandboxing failed!
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc:129 No usable sandbox!
```

같은 메시지는 root 사용자로 실행했을 때도 나옵니다. `--doctor`가 두 경우를 구분합니다.

## 빠른 해결

```bash
./aribrowser --doctor                 # 원인 확인 (summary.error_code)
sudo ./aribrowser --install-sandbox   # AriBrowser 전용 AppArmor 정책 설치 + 호출 사용자 권한으로 검증
./aribrowser --self-test              # 샌드박스가 켜진 상태로 통과해야 합니다
```

`--install-sandbox`는 `/etc/apparmor.d/aribrowser-chromium`에 아래와 같은 정책을 쓰고 `apparmor_parser -r`로 적재합니다. Ubuntu가 자체 Chrome/Brave에 쓰는 것과 같은 방식이며 번들 Chromium에만 적용됩니다.

```text
# aribrowser-root: /opt/aribrowser/_internal/playwright/driver/package/.local-browsers
abi <abi/4.0>,
include <tunables/global>

profile aribrowser-chromium /opt/aribrowser/_internal/playwright/driver/package/.local-browsers/**/{chrome,chrome-headless-shell} flags=(unconfined) {
  userns,
}
```

root가 아니면 정책 본문과 설치 명령을 출력하고 종료 코드 2를 돌려줍니다. AppArmor가 user namespace를 제한하지 않는 커널(Ubuntu 22.04 등)에서는 `status=not_needed`로 끝납니다. `sudo`로 실행하면 HOME이 root로 바뀌므로 소스 실행 시에는 호출 사용자(`SUDO_USER`)의 Playwright 캐시 경로를 다시 해석하고, 설치 뒤 검증은 호출 사용자 권한으로 내려서 수행합니다.

### 대안: setuid 샌드박스 도우미

```bash
sudo ./aribrowser --install-sandbox --sandbox-method suid
```

Chromium이 제공하는 `chrome_sandbox`를 `chrome-sandbox`(root:root, 4755)로 설치하고, 실행 시 `CHROME_DEVEL_SANDBOX`로 알려줍니다. user namespace 제한과 무관하게 동작하며 설치 디렉터리를 따라다닙니다. 다만 setuid 바이너리를 두는 방식이므로 AppArmor 정책을 우선 권장합니다.

## 브라우저 업데이트 이후

정책은 실행 파일의 절대 경로가 아니라 **브라우저 루트 디렉터리 아래 glob**(`**/{chrome,chrome-headless-shell}`)에 붙습니다. 같은 설치 디렉터리에서 Chromium 리비전이 바뀌어도 그대로 유효합니다. 설치 디렉터리를 옮기거나 새 디렉터리에 새 버전을 풀면 정책 파일 머리글의 `aribrowser-root`와 현재 경로가 달라지며, `--doctor`가 `SANDBOX_PROFILE_STALE`로 알립니다. 그때 `sudo ./aribrowser --install-sandbox`를 다시 실행하면 같은 이름의 정책을 덮어써 오래된 정책과 충돌하지 않습니다. 정책을 완전히 제거하려면 `sudo ./aribrowser --uninstall-sandbox`를 실행합니다.

## 진단 코드

| 코드 | 의미 | 조치 |
| --- | --- | --- |
| `CHROMIUM_MISSING` | 번들 Chromium 실행 파일이 없음 | 배포 파일을 전체 해제하고 `_internal`을 유지 |
| `CHROMIUM_DEPENDENCIES_MISSING` | 공유 라이브러리 누락 | `python -m playwright install-deps chromium` 또는 README의 패키지 설치 |
| `SANDBOX_ROOT_USER` | root 사용자는 샌드박스를 쓸 수 없음 | 일반 사용자로 실행 |
| `SANDBOX_USERNS_RESTRICTED` | AppArmor가 비특권 userns를 제한하고 정책이 없음 | `sudo ./aribrowser --install-sandbox` |
| `SANDBOX_PROFILE_STALE` | 정책은 있지만 현재 Chromium 경로를 포함하지 않음 | `sudo ./aribrowser --install-sandbox`로 갱신 |
| `SANDBOX_PROFILE_NOT_LOADED` | 정책 파일은 있지만 커널에 적재되지 않음 | `sudo apparmor_parser -r /etc/apparmor.d/aribrowser-chromium` |
| `SANDBOX_LAUNCH_FAILED` | 그 외 샌드박스 실행 실패 | doctor의 `sandbox.message` 확인 |
| `DISPLAY_MISSING` | `--headed`인데 DISPLAY/WAYLAND_DISPLAY 없음 | headless로 실행하거나 디스플레이 설정 |
| `PROFILE_LOCKED` | 다른 프로세스가 프로필 사용 중 | 다른 data-dir/profile 사용 또는 `--cleanup` |
| `BROWSER_LAUNCH_FAILED` | 분류되지 않은 실행 실패 | doctor 출력의 `log` 확인 |

`--doctor`는 위 코드를 `checks[].code`와 `summary.error_code`로 돌려주고, 세션 중 프로필 실행 실패(`navigate`/`profile.create`)도 같은 코드와 `details.fix`로 보고합니다.

## 샌드박스 활성 근거

Chromium은 사용 가능한 샌드박스가 없으면 `--no-sandbox` 없이 시작하지 못하므로 정상 시작 자체가 샌드박스 활성의 근거입니다. `--doctor`는 추가로 렌더러 프로세스의 `/proc/<pid>/status`에서 `Seccomp: 2`(필터 모드)와 `NoNewPrivs: 1`을 읽어 `sandbox.seccomp`에 돌려줍니다.

## 프로세스와 잠금

- `close`/`profile.close`는 Chromium 주 프로세스와 자손(렌더러/GPU/유틸리티)의 종료를 최대 3초 기다린 뒤 남은 프로세스를 종료하고 `processes.terminated/forced/remaining`을 돌려줍니다. 정상 종료 뒤에는 `SingletonLock`이 남지 않습니다.
- 프로필을 열 때 잔여 잠금(소유 프로세스가 없거나 다른 호스트 이름)은 자동으로 제거하고 `profile_lock_recovered` 이벤트를 남깁니다. 살아 있는 프로세스의 잠금은 `PROFILE_LOCKED` 오류입니다.
- `./aribrowser --cleanup`과 프로토콜 `session.cleanup`은 같은 data-dir를 쓰는 이전 세션의 Chromium 프로세스를 종료하고 잔여 잠금을 제거합니다. 현재 세션이 연 프로세스는 건드리지 않습니다.
- `--doctor`의 `processes`/`profiles`에서 잔여 프로세스와 잠금 상태를 먼저 확인할 수 있습니다.
