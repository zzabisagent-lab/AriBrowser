# Linux Chromium 샌드박스 설치와 진단

AriBrowser는 Chromium의 보안 격리(샌드박스)를 켠 채 실행하며 어떤 실패 경로에서도 `--no-sandbox`를 자동으로 켜지 않습니다. Ubuntu 23.10/24.04 이상은 AppArmor로 비특권 user namespace를 제한하므로(`kernel.apparmor_restrict_unprivileged_userns=1`) 아무 설정 없이 실행하면 다음 오류가 납니다.

```text
Chromium sandboxing failed!
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc:129 No usable sandbox!
```

같은 메시지는 root 사용자로 실행했을 때도 나옵니다. `--doctor`가 두 경우를 구분합니다.

## 0.2.0: 호스트당 한 번만 설치

0.1.0의 AppArmor 정책은 설치 디렉터리 경로에 붙어 있어 AriBrowser를 새 경로에 풀 때마다 다시 설치해야 했습니다. 0.2.0은 세 가지 경로 독립 방법을 제공하며 어느 것이든 **한 번** 하면 그 호스트의 모든 AriBrowser 설치(경로·Chromium 리비전·사용자 무관)에 적용됩니다.

| 방법 | 명령 | 동작 | 적용 범위 |
| --- | --- | --- | --- |
| 공용 setuid 도우미(기본) | `sudo ./aribrowser --install-sandbox` | Chromium이 제공하는 `chrome_sandbox`를 `/usr/local/lib/aribrowser/chrome-sandbox`(root:root, 4755)에 복사. 실행 시 `CHROME_DEVEL_SANDBOX`로 전달 | 모든 경로·리비전·사용자. AppArmor 유무와 userns 제한 무관 |
| 경로 독립 AppArmor 정책 | `sudo ./aribrowser --install-sandbox --sandbox-method apparmor` | `/etc/apparmor.d/aribrowser-chromium`에 `userns` 허용 정책 설치. 모든 사용자의 Playwright 캐시와 배포 디렉터리 구조에 붙음 | `/home/*/.cache/ms-playwright`, `/root/.cache/ms-playwright`, `/**/_internal/playwright/driver/package/.local-browsers` 아래의 `chrome`/`chrome-headless-shell` |
| 시스템 브라우저 대체 | 설치 없음(`--browser auto`, 기본) | 번들 Chromium이 샌드박스 문제로 실행되지 않으면 설치된 Google Chrome/Chromium/Edge/Brave로 실행 | 배포판이 이미 허용한 브라우저. Snap Chromium은 홈 숨김 디렉터리 접근 제한으로 후순위 |

```bash
./aribrowser --doctor                 # 원인 확인 (summary.error_code, sandbox_helper, system_browsers)
sudo ./aribrowser --install-sandbox   # 공용 도우미 설치 + 호출 사용자 권한으로 번들 Chromium 실행 검증
./aribrowser --self-test              # 샌드박스가 켜진 상태로 통과해야 합니다
```

공용 도우미는 Google Chrome의 .deb 패키지가 쓰는 것과 같은 setuid 샌드박스이며 그 API는 오래전부터 동결되어 있어 Chromium 버전이 바뀌어도 그대로 동작합니다. user namespace가 허용된 커널에서는 Chromium이 namespace 샌드박스를 우선하므로 도우미를 항상 전달해도 해가 없습니다. `--uninstall-sandbox`는 도우미와 정책을 함께 제거합니다.

### AppArmor 정책 본문

```text
# aribrowser-roots: /home/*/.cache/ms-playwright;/root/.cache/ms-playwright;/**/_internal/playwright/driver/package/.local-browsers
abi <abi/4.0>,
include <tunables/global>

profile aribrowser-chromium /{home/*/.cache/ms-playwright,root/.cache/ms-playwright,**/_internal/playwright/driver/package/.local-browsers}/**/{chrome,chrome-headless-shell} flags=(unconfined) {
  userns,
}
```

표준 패턴 밖의 경로에 Chromium을 두었다면(`--chromium <경로>`) 설치 시 그 브라우저 루트가 패턴에 추가됩니다. root가 아니면 정책 본문과 설치 명령을 출력하고 종료 코드 2를 돌려줍니다. AppArmor가 user namespace를 제한하지 않는 커널(Ubuntu 22.04 등)에서는 `status=not_needed`로 끝납니다. `sudo`로 실행하면 HOME이 root로 바뀌므로 소스 실행 시에는 호출 사용자(`SUDO_USER`)의 Playwright 캐시 경로를 다시 해석하고, 설치 뒤 검증은 호출 사용자 권한으로 내려서 `--browser bundled`로 수행합니다(대체 실행이 아니라 번들 Chromium 자체를 검증).

### 0.1.0 방식(`--sandbox-method suid`)

Chromium 디렉터리 안에 `chrome-sandbox`를 설치합니다. 설치 디렉터리를 따라다니므로 새 경로마다 다시 설치해야 합니다. 호환을 위해 남겨 두었습니다.

## 시스템 브라우저 대체(`--browser`)

- `auto`(기본): 번들 Chromium을 먼저 시도하고, `SANDBOX_*`/`CHROMIUM_DEPENDENCIES_MISSING`/`CHROMIUM_MISSING`으로 실패하면 시스템 브라우저를 순서대로 시도합니다. 대체는 `browser_fallback`(사유 코드)과 `browser_selected` 이벤트, `capabilities.browser{kind,executable,fallback_from,sandbox_helper}`로 알립니다.
- `bundled`: 번들 Chromium만. 실패는 그대로 오류입니다(`details.tried`에 시도 내역).
- `system`: 시스템 브라우저만. 없으면 `SYSTEM_BROWSER_MISSING`.
- `--chromium <경로>`: 그 실행 파일만 씁니다.

시스템 브라우저는 자체 샌드박스 구성(배포판 AppArmor 프로필 또는 자체 setuid 도우미)을 사용하므로 AriBrowser의 정책/도우미 판정을 적용하지 않고 실제 실행 검사로만 판정합니다. `--doctor`는 `--browser auto`에서 번들 실행이 실패하면 시스템 브라우저로 같은 검사를 반복해 `sandbox.fallback`에 담고 `sandbox_launch`를 경고(`warn`)로 낮춥니다. 대체 실행 역시 `--no-sandbox`가 아닙니다.

## 브라우저 업데이트 이후

공용 도우미는 Chromium 리비전과 무관합니다. AppArmor 정책은 브라우저 루트 아래 glob에 붙으므로 같은 캐시/배포 구조 안의 리비전 변경에 영향을 받지 않습니다. 표준 패턴 밖의 새 경로를 쓰면 `--doctor`가 `SANDBOX_PROFILE_STALE`로 알리며, 그때는 `sudo ./aribrowser --install-sandbox`(도우미) 한 번으로 해결됩니다. 재설치는 같은 이름의 정책을 덮어써 오래된 정책과 충돌하지 않습니다.

## 진단 코드

| 코드 | 의미 | 조치 |
| --- | --- | --- |
| `CHROMIUM_MISSING` | 번들 Chromium 실행 파일이 없음 | 배포 파일을 전체 해제하고 `_internal`을 유지 |
| `CHROMIUM_DEPENDENCIES_MISSING` | 공유 라이브러리 누락 | `python -m playwright install-deps chromium` 또는 README의 패키지 설치 |
| `SANDBOX_ROOT_USER` | root 사용자는 샌드박스를 쓸 수 없음 | 일반 사용자로 실행 |
| `SANDBOX_USERNS_RESTRICTED` | 비특권 userns가 제한되고 도우미/정책이 없음 | `sudo ./aribrowser --install-sandbox` 또는 시스템 브라우저 설치(`--browser auto`) |
| `SANDBOX_HELPER_INVALID` | 공용 도우미가 있지만 root 소유 4755가 아님 | `sudo ./aribrowser --install-sandbox`로 재설치 |
| `SANDBOX_PROFILE_STALE` | 정책은 있지만 현재 Chromium 경로를 포함하지 않음 | `sudo ./aribrowser --install-sandbox`(도우미) 또는 `--sandbox-method apparmor`로 갱신 |
| `SANDBOX_PROFILE_NOT_LOADED` | 정책 파일은 있지만 커널에 적재되지 않음 | `sudo apparmor_parser -r /etc/apparmor.d/aribrowser-chromium` |
| `SANDBOX_LAUNCH_FAILED` | 그 외 샌드박스 실행 실패 | doctor의 `sandbox.message` 확인 |
| `SYSTEM_BROWSER_MISSING` | `--browser system`인데 설치된 브라우저가 없음 | `--browser auto` 또는 `--chromium <경로>` |
| `DISPLAY_MISSING` | `--headed`인데 DISPLAY/WAYLAND_DISPLAY 없음 | headless로 실행하거나 디스플레이 설정 |
| `PROFILE_LOCKED` | 다른 프로세스가 프로필 사용 중 | 다른 data-dir/profile 사용 또는 `--cleanup` |
| `BROWSER_LAUNCH_FAILED` | 분류되지 않은 실행 실패 | doctor 출력의 `log` 확인 |

`--doctor`는 위 코드를 `checks[].code`와 `summary.error_code`로 돌려주고, 세션 중 프로필 실행 실패(`navigate`/`profile.create`)도 같은 코드와 `details.fix`로 보고합니다. 실제 실행이 성공한 경우(도우미, 시스템 브라우저) 정적 AppArmor 판정의 실패는 `info`로 낮춥니다.

## 샌드박스 활성 근거

Chromium은 사용 가능한 샌드박스가 없으면 `--no-sandbox` 없이 시작하지 못하므로 정상 시작 자체가 샌드박스 활성의 근거입니다. `--doctor`는 추가로 렌더러 프로세스의 `/proc/<pid>/status`에서 `Seccomp: 2`(필터 모드)와 `NoNewPrivs: 1`을 읽어 `sandbox.seccomp`에 돌려주고, `sandbox.sandbox_source`로 setuid 도우미(`setuid_helper`)와 namespace/정책(`namespace_or_profile`) 중 무엇이 쓰였는지 표시합니다.

## 프로세스와 잠금

- `close`/`profile.close`는 Chromium 주 프로세스와 자손(렌더러/GPU/유틸리티)의 종료를 최대 3초 기다린 뒤 남은 프로세스를 종료하고 `processes.terminated/forced/remaining`을 돌려줍니다. 정상 종료 뒤에는 `SingletonLock`이 남지 않습니다.
- 프로필을 열 때 잔여 잠금(소유 프로세스가 없거나 다른 호스트 이름)은 자동으로 제거하고 `profile_lock_recovered` 이벤트를 남깁니다. 살아 있는 프로세스의 잠금은 `PROFILE_LOCKED` 오류입니다.
- `./aribrowser --cleanup`과 프로토콜 `session.cleanup`은 같은 data-dir를 쓰는 이전 세션의 Chromium 프로세스를 종료하고 잔여 잠금을 제거합니다. 현재 세션이 연 프로세스는 건드리지 않습니다.
- `--doctor`의 `processes`/`profiles`에서 잔여 프로세스와 잠금 상태를 먼저 확인할 수 있습니다.
