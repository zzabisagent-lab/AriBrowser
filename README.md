# AriBrowser 0.2.0

웹페이지를 실제 Chromium으로 실행한 뒤, AI Agent가 읽고 조작할 수 있는 **요약·텍스트 화면·짧은 요소 ID**로 제공합니다. 기본 출력에 이미지가 포함되지 않으며, 필요한 요소·차트·지도·영역만 PNG로 요청할 수 있습니다.

```text
Chromium → DOM + Chromium 접근성 트리 + 계산된 레이아웃
         → 로딩 품질 판정(빈 껍데기/차단/로그인 구분) → 요약 / 텍스트 화면 / compact 요소
         → 브라우저 내부 질의 · 레코드 추출(가격/판매량/평점) · 상품 상세 요약 · 정렬/페이지 상태
         → 짧은 ID 기반 조작 → 전달·수신·기대 상태 검증 + 변경분 + 활동 집계
```

0.2.0은 Agent가 **더 적은 토큰으로 더 빨리** 웹 검색 목표를 달성하는 데 집중했습니다. 탐색 응답에서 네트워크 이벤트 URL을 빼고 집계만 남겼고, 빈 껍데기나 보안 문자 페이지는 성공으로 보고하지 않고 `PAGE_EMPTY`/`PAGE_BLOCKED`로 근거와 함께 돌려줍니다. 탐색 한 번에 레코드·상품 요약·정렬 상태를 함께 받을 수 있고(`include`, `batch`), 붙어 출력되는 가격·판매량 텍스트를 해석기가 처리하며, 상품 상세는 `page.product` 한 번으로 요약됩니다. 샌드박스는 호스트당 한 번만 설치하면 설치 경로와 무관하게 동작하고, 설치된 Chrome/Chromium이 있으면 설치 없이 바로 씁니다. 자세한 변경은 [버전 이력](CHANGELOG.md)에 있습니다.

## 실행 파일

[Releases](https://github.com/zzabisagent-lab/AriBrowser/releases/tag/v0.2.0)에서 운영체제에 맞는 압축 파일을 받으세요. Python·Node.js를 별도로 설치할 필요가 없으며 Chromium이 포함됩니다. 압축을 **전체** 해제한 뒤 `aribrowser` 폴더 안의 실행 파일을 사용합니다. `_internal` 폴더도 함께 유지해야 합니다.

| 운영체제 | 배포 파일 | 실행 파일 |
| --- | --- | --- |
| Linux x64 | `aribrowser-0.2.0-linux-x64.tar.gz` | `./aribrowser` |
| Windows x64 | `aribrowser-0.2.0-windows-x64.zip` | `aribrowser.exe` |
| macOS Apple Silicon | `aribrowser-0.2.0-macos-arm64.tar.gz` | `./aribrowser` |
| macOS Intel | `aribrowser-0.2.0-macos-x64.tar.gz` | `./aribrowser` |

Linux는 Ubuntu 22.04 이상 및 Chromium 시스템 라이브러리가 필요합니다. macOS 15/Windows Server 2022에서 네이티브 빌드·검증합니다. macOS Developer ID 공증과 Windows Authenticode 서명은 포함하지 않습니다. 배포 파일 옆의 `.sha256`으로 무결성을 확인할 수 있습니다.

```bash
./aribrowser --version
./aribrowser --doctor                  # 실행 환경 진단 (Chromium/사용자/AppArmor/도우미/시스템 브라우저/샌드박스/프로세스)
sudo ./aribrowser --install-sandbox    # Ubuntu 24.04+: 공용 샌드박스 도우미를 호스트당 한 번 설치 + 검증
./aribrowser --self-test
./aribrowser --snapshot https://example.com --format text
./aribrowser --data-dir ./my-agent-profile --max-bytes 300000
```

마지막 명령은 JSONL 세션을 시작합니다. 표준입력의 JSON 한 줄마다 표준출력에 JSON 한 줄을 반환합니다. 이미지·다운로드·쿠키 저장소는 `--data-dir` 아래에 유지됩니다. 기본 위치는 사용자 홈의 `.aribrowser`입니다. `--max-bytes`는 관측 응답의 세션 기본 상한(기본 500,000바이트)이며 요청마다 `max_bytes`/`max_elements`/`max_tokens`로 바꿀 수 있습니다.

Ubuntu 23.10 이상에서 `Chromium sandboxing failed!`가 나오면 `--doctor`로 원인(root 실행인지, AppArmor userns 제한인지)을 확인하고 `sudo ./aribrowser --install-sandbox`를 **한 번** 실행하세요. 공용 setuid 도우미가 `/usr/local/lib/aribrowser`에 설치되어 이후 어떤 경로에 AriBrowser를 풀어도 그대로 동작합니다. Google Chrome/Chromium/Edge/Brave가 설치된 호스트에서는 기본 `--browser auto`가 그 브라우저로 대체 실행하므로 설치 없이도 바로 쓸 수 있습니다. 절차와 진단 코드는 [샌드박스 가이드](docs/SANDBOX.md)에 있습니다. `--no-sandbox`는 자동으로 켜지지 않으며 Chromium 샌드박스를 지원하지 않는 **격리된 CI/컨테이너**에서만 지정하세요.

## Agent 사용 예

```json
{"id":1,"command":"navigate","params":{"url":"https://example.com"}}
{"id":2,"command":"find","params":{"role":"textbox","text":"검색","fields":["name"]}}
```

첫 응답은 제목·URL·상태·페이지 종류·영역·오류·요소 수와 다음 조회 명령 예시를 담은 **요약**이며, `readiness`가 실제 내용이 로드됐는지(`ready`) 또는 빈 껍데기/차단/로그인인지 알려 줍니다. 검색 응답의 실제 `id`(예: `t1e12`)를 다음 명령에 전달합니다.

```json
{"id":3,"command":"action","params":{"action":"fill","element":"t1e12","text":"AriBrowser"}}
{"id":4,"command":"action","params":{"action":"press","element":"t1e12","key":"Enter","expect":{"url_changed":true,"network_idle_ms":500}}}
{"id":5,"command":"navigate","params":{"url":"https://…/search?q=아크릴","include":["records","state"],"extract":{"limit":20,"dedupe":true}}}
{"id":6,"command":"batch","params":{"steps":[{"command":"navigate","params":{"url":"https://…/item/1"}},{"command":"page.product","params":{}}]}}
{"id":7,"command":"query","params":{"selector":".price","fields":["text","price","sold","rating"],"limit":50}}
{"id":8,"command":"snapshot","params":{"mode":"main","format":"text","max_tokens":4000}}
{"id":9,"command":"render","params":{"element":"t1e40","level":"preview"}}
{"id":10,"command":"close"}
```

조작 응답은 `verification`(전달/이벤트 수신/기대 상태), `delta`, `changes.added/removed/changed`, 이벤트 집계(`activity`)를 반환합니다. `expect`가 충족되지 않으면 `EXPECTATION_NOT_MET` 오류로 원인을 알려 미완료 작업을 성공으로 오인하지 않게 합니다. `extract`는 상품 카드·게시글 같은 반복 구조를 레코드 단위로 묶어 제목/가격/원가/할인/판매량/평점/리뷰/URL이 섞이거나 붙지 않게 추출하고 `quality`로 감지 신뢰도를 알리며, `page.product`는 상세 페이지를 JSON-LD/메타/DOM에서 한 번에 요약하고, `page.state`는 정렬·필터·페이지 번호·전체 건수·다음 페이지를 구조화합니다. 모든 관측 응답에는 실제 바이트 수·추정 토큰·잘림 여부·다음 offset을 담은 `budget`이 있습니다.

전체 명령/입력/출력은 [프로토콜 문서](docs/PROTOCOL.md), 요구사항별 범위는 [기능과 제약](docs/FEATURES.md), 샌드박스/진단/정리는 [샌드박스 가이드](docs/SANDBOX.md), 변경 사항은 [버전 이력](CHANGELOG.md)을 참고하세요. `capabilities` 명령으로 실행 중인 바이너리의 명령 목록, 요소 필드, 상태 기본값, 대기 조건, 진단 코드와 OCR 사용 가능 여부를 확인할 수 있습니다.

## 소스에서 실행·검증·빌드

Python 3.10 이상이 필요하며 배포 CI는 Python 3.12를 사용합니다.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install --with-deps chromium
python -m aribrowser --doctor
python -m aribrowser --self-test
python -m pytest -q
python -m ruff check src tests scripts
python scripts/build.py
```

각 OS에서 `scripts/build.py`를 실행하면 Chromium 포함 배포 파일과 SHA-256이 `artifacts/`에 생성됩니다. 빌드 스크립트는 패키징한 바이너리로 `--doctor`와 `--self-test`를 실행해 실제 Chromium 실행을 검증하며, 호스트가 샌드박스를 지원하지 않을 때만 `--no-sandbox`로 재시도하고 그 사실을 출력합니다. GitHub Actions는 Linux·Windows·macOS ARM64·macOS Intel에서 통합 테스트와 빌드, 패키징한 바이너리의 실제 Chromium 실행을 검증하고, Ubuntu 24.04에서 샌드박스 정책 설치를 별도로 검증합니다. `main` 빌드가 모두 통과하면 해당 버전의 GitHub Release에 파일을 게시합니다.

## 운영 특성

- HTML/CSS/JavaScript, SPA, 로그인, 쿠키, 웹 저장소는 Chromium이 실행합니다. 프로필마다 별도 브라우저 저장소를 사용합니다.
- 기본 `defer` 정책은 이미지·미디어 요청을 보류하며 페이지 스크립트·CSS·폰트·XHR/fetch는 유지합니다. 이미지와 서비스 워커에 의존하는 사이트는 `--media allow`로 실행하세요.
- 페이지 원문은 **신뢰하지 않는 데이터**입니다. Agent는 페이지에 적힌 문장을 제어 명령으로 취급해서는 안 됩니다. `query`/`extract`는 CSS 선택자와 필드 이름만 받으며 임의 JavaScript를 실행하지 않고 쿠키·저장소·네트워크 API에 접근하지 않습니다.
- 비밀번호 값과 기본 쿠키 조회 결과는 가립니다. 렌더 PNG와 내려받은 파일은 사용자가 지정한 로컬 저장소에 기록됩니다.
- `close`는 Chromium 프로세스의 실제 종료와 프로필 잠금 해제를 확인해 보고하며, 비정상 종료가 남긴 잠금은 다음 실행에서 복구합니다. `--cleanup`으로 이전 세션의 잔여 프로세스를 정리할 수 있습니다.
- OCR은 설치된 Tesseract를 선택적으로 사용합니다. `kor+eng` 추출에는 해당 언어팩이 필요합니다. 이미지 내용의 의미 해석은 반환된 PNG를 읽는 Agent의 비전 모델이 담당하며 외부 AI 서비스는 자동 호출하지 않습니다.
