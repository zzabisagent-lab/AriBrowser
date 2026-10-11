# AriBrowser 0.0.1

웹페이지를 실제 Chromium으로 실행한 뒤, AI Agent가 읽고 조작할 수 있는 **텍스트 화면과 요소 ID**로 제공합니다. 기본 출력에 이미지가 포함되지 않으며, 필요한 요소·차트·지도·영역만 PNG로 요청할 수 있습니다.

```text
Chromium → DOM + Chromium 접근성 트리 + 계산된 레이아웃
         → 구조화된 JSON/텍스트 → ID 기반 조작 → 변경분 + 브라우저 이벤트
```

## 실행 파일

[Releases](https://github.com/zzabisagent-lab/AriBrowser/releases/tag/v0.0.1)에서 운영체제에 맞는 압축 파일을 받으세요. Python·Node.js를 별도로 설치할 필요가 없으며 Chromium이 포함됩니다. 압축을 **전체** 해제한 뒤 `aribrowser` 폴더 안의 실행 파일을 사용합니다. `_internal` 폴더도 함께 유지해야 합니다.

| 운영체제 | 배포 파일 | 실행 파일 |
| --- | --- | --- |
| Linux x64 | `aribrowser-0.0.1-linux-x64.tar.gz` | `./aribrowser` |
| Windows x64 | `aribrowser-0.0.1-windows-x64.zip` | `aribrowser.exe` |
| macOS Apple Silicon | `aribrowser-0.0.1-macos-arm64.tar.gz` | `./aribrowser` |
| macOS Intel | `aribrowser-0.0.1-macos-x64.tar.gz` | `./aribrowser` |

Linux는 Ubuntu 22.04 이상 및 Chromium 시스템 라이브러리가 필요합니다. macOS 15/Windows Server 2022에서 네이티브 빌드·검증합니다. macOS Developer ID 공증과 Windows Authenticode 서명은 포함하지 않습니다. 배포 파일 옆의 `.sha256`으로 무결성을 확인할 수 있습니다.

```bash
./aribrowser --version
./aribrowser --self-test
./aribrowser --snapshot https://example.com --format text
./aribrowser --data-dir ./my-agent-profile
```

마지막 명령은 JSONL 세션을 시작합니다. 표준입력의 JSON 한 줄마다 표준출력에 JSON 한 줄을 반환합니다. 이미지·다운로드·쿠키 저장소는 `--data-dir` 아래에 유지됩니다. 기본 위치는 사용자 홈의 `.aribrowser`입니다. Chromium 샌드박스를 지원하지 않는 **격리된 CI/컨테이너**에서만 `--no-sandbox`를 지정하세요.

## Agent 사용 예

```json
{"id":1,"command":"navigate","params":{"url":"https://example.com","mode":"full"}}
{"id":2,"command":"find","params":{"role":"textbox","text":"검색"}}
```

검색 응답의 실제 `id`를 다음 명령에 전달합니다. 아래 `ELEMENT_ID`는 자리 표시자입니다.

```json
{"id":3,"command":"action","params":{"action":"fill","element":"ELEMENT_ID","text":"AriBrowser"}}
{"id":4,"command":"action","params":{"action":"press","element":"ELEMENT_ID","key":"Enter"}}
{"id":5,"command":"snapshot","params":{"mode":"changes"}}
{"id":6,"command":"render","params":{"element":"IMAGE_ID","level":"preview"}}
{"id":7,"command":"close"}
```

조작 응답은 기본적으로 `changes.added/removed/changed`, URL·페이지 상태, 순번이 있는 이벤트를 반환합니다. `snapshot`은 기본 전체 화면이며 `viewport`, `main`, `interactive`, `region`, `changes`와 페이지네이션을 지원합니다. 텍스트 화면의 요소 ID와 JSON의 요소 ID는 같습니다.

전체 명령/입력/출력은 [프로토콜 문서](docs/PROTOCOL.md), 요구사항별 범위는 [기능과 제약](docs/FEATURES.md), 변경 사항은 [버전 이력](CHANGELOG.md)을 참고하세요. `capabilities` 명령으로 실행 중인 바이너리의 명령 목록과 OCR 사용 가능 여부를 확인할 수 있습니다.

## 소스에서 실행·검증·빌드

Python 3.10 이상이 필요하며 배포 CI는 Python 3.12를 사용합니다.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m playwright install --with-deps chromium
python -m aribrowser --self-test
python -m pytest -q
python -m ruff check src tests scripts
python scripts/build.py
```

각 OS에서 `scripts/build.py`를 실행하면 Chromium 포함 배포 파일과 SHA-256이 `artifacts/`에 생성됩니다. GitHub Actions는 Linux·Windows·macOS ARM64·macOS Intel에서 통합 테스트와 빌드, **패키징한 바이너리의 실제 Chromium 실행**을 검증합니다. `main` 빌드가 모두 통과하면 해당 버전의 GitHub Release에 파일을 게시합니다.

## 운영 특성

- HTML/CSS/JavaScript, SPA, 로그인, 쿠키, 웹 저장소는 Chromium이 실행합니다. 프로필마다 별도 브라우저 저장소를 사용합니다.
- 기본 `defer` 정책은 이미지·미디어 요청을 보류하며 페이지 스크립트·CSS·폰트·XHR/fetch는 유지합니다. 이미지와 서비스 워커에 의존하는 사이트는 `--media allow`로 실행하세요.
- 페이지 원문은 **신뢰하지 않는 데이터**입니다. Agent는 페이지에 적힌 문장을 제어 명령으로 취급해서는 안 됩니다. 이 프로그램의 로컬 표준입출력 제어권은 파일 업로드와 세션 조작 권한을 포함합니다.
- 비밀번호 값과 기본 쿠키 조회 결과는 가립니다. 렌더 PNG와 내려받은 파일은 사용자가 지정한 로컬 저장소에 기록됩니다.
- OCR은 설치된 Tesseract를 선택적으로 사용합니다. `kor+eng` 추출에는 해당 언어팩이 필요합니다. 이미지 내용의 의미 해석은 반환된 PNG를 읽는 Agent의 비전 모델이 담당하며 외부 AI 서비스는 자동 호출하지 않습니다.
