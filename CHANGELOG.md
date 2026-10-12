# 버전 이력

## 0.1.0 — 2026-10-12

AI Agent의 토큰 사용량 절감, 작업 결과의 정확성, 무인 운영 안정성을 목표로 한 개선 릴리스입니다. 실사용 검증 보고서의 P0~P2 15개 요구사항을 구현했습니다. 기본 동작이 바뀐 항목은 `docs/PROTOCOL.md`의 "0.1.0에서 달라진 기본 동작"을 참고하세요.

### 실측 효과 (다나와 "아크릴" 검색 결과, 같은 페이지·같은 세션)

| 명령 | 0.0.1 방식 | 0.1.0 기본 |
| --- | --- | --- |
| 첫 접속 `navigate` | 4,708,712바이트 / 약 1,191,833토큰 (`mode=changes`, 전체 필드) | 4,095바이트 / 약 1,056토큰 (`mode=summary`) |
| `snapshot(mode=main, limit=3000)` | 2,444,213바이트 / 약 624,522토큰 (요소+텍스트, 전체 필드) | 401,685바이트 / 약 105,517토큰 (`format=elements` compact), 240,975바이트 / 약 65,066토큰 (`format=text`) |
| `find(text="아크릴", role="listitem")` | 94,388바이트 / 약 24,852토큰 | 25,858바이트 / 약 7,510토큰 (compact), 3,563바이트 / 약 1,053토큰 (`fields=["name"], limit=20`) |
| `extract(limit=20)` 상품 20건(제목/URL/가격/이미지) | 해당 없음 | 12,300바이트 / 약 3,434토큰, 0.09초 |
| `page.state` 정렬/페이지/페이지당 수/토글 | 해당 없음 | 2,570바이트 / 약 667토큰 |

토큰은 외부 tokenizer 없이 문자 종류별 가중치로 추정한 값(`heuristic-v1`)이며 실제 모델과 다를 수 있습니다. 같은 페이지의 로딩 중 DOM(약 60,000노드, 36,849개 의미 요소)에서 0.0.1의 수집기는 결과 전송에 75초 이상 걸려 관측이 시간 초과됐으나, 0.1.0은 수집기 압축 형식과 문자열 전송으로 약 6초 안에 관측을 끝냅니다.

### P0 — 핵심 기능 및 결과 정확성

- **P0-1 Navigation 응답 크기 제한**: `navigate`의 기본 `mode`를 `summary`로 바꿔 제목·URL·로딩/HTTP 상태·인증 추정·주요 영역(랜드마크)·제목 목록·검색창·오류(HTTP/알림/유효성/오류 이벤트/경고)·요소 수와 생략된 요소 수, 추가 조회 명령 예시(`omitted.next`)만 돌려줍니다. `max_regions`/`max_headings`로 요약 크기를 제한하고, `mode=changes`/`full` 등 상세는 요청할 때만 반환하며 모든 모드에 요소 수/바이트/토큰 예산이 적용됩니다. `tab.new`/`tab.switch`도 요약을 돌려줍니다. 탐색 후 `load` 이벤트를 최대 10초(또는 `--timeout-ms`)까지 짧게 기다려 로딩 중 DOM을 읽는 일을 줄였습니다.
- **P0-2 Snapshot 경량화**: `format=summary|text|elements|both`를 추가하고 기본을 `text`로 바꿨습니다. `fields`로 `id`/`role`/`name`/`href` 등 필요한 필드만 고를 수 있고, 기본 `compact=true`는 빈 필드·기본 상태값·이름과 같은 텍스트·디버깅 필드(`bounds`/`groups`/`labels`/`style`/`accessibility_source`)를 생략합니다. 상태 기본값은 `capabilities.state_defaults`와 프로토콜 문서에 명시했습니다. 대용량 결과는 `offset`/`limit`과 예산의 `next_offset`으로 분할 조회합니다. 텍스트 화면 한 줄에 링크 `href`를 함께 표시합니다.
- **P0-3 반복 레코드 구조화 추출**: `extract` 명령을 추가했습니다. 같은 서명(태그+클래스)을 가진 형제가 3개 이상인 그룹을 자동 감지하거나 `container`/`item` 선택자로 지정하고, 레코드마다 `title`/`url`/`price`/`shipping`/`image`/`text` 자동 필드 또는 `{selector, attr, type(text|number|price|url), all, regex}` 명세로 필드를 읽습니다. 필드는 해당 레코드 요소 안에서만 읽어 레코드 간 혼합이 없고, 누락 필드는 `null`과 `missing` 원인(`no_match`/`attribute_empty`/`empty_text`/`regex_no_match`)으로 표시하며 `dedupe`로 중복 레코드를 제거합니다. 가격 해석은 "200ml 6,200원"처럼 통화 표시가 없는 숫자를 건너뜁니다. 레코드 ID는 바로 `query`의 `scope`나 `action`에 쓸 수 있습니다.
- **P0-4 Action 실행 결과 검증**: 조작 응답에 `verification`을 추가했습니다. `action_dispatched`(입력 전달), `event_received`(조작 직전 설치한 캡처 리스너가 click/input/change/keydown 등 DOM 이벤트를 실제로 받았는지, `events` 목록 포함), `navigation`(탐색 발생), `expected_state_reached`(`expect` 조건 도달)를 구분합니다. `expect`가 충족되지 않으면 `EXPECTATION_NOT_MET` 오류로 `unmet`/`met`/`reason`/`page`를 돌려주어 미완료 작업을 성공으로 오인하지 않게 했습니다. `delta.url`로 조작 전후 URL을, 변경분으로 요소 수준 차이를 비교할 수 있습니다. `verify=false`로 탐지기 설치를 끌 수 있습니다.
- **P0-5 정렬·필터·페이지 상태 검증**: `page.state` 명령을 추가했습니다. URL 매개변수, 네이티브 select, 활성 클래스/aria 상태를 가진 정렬 컨트롤에서 현재 정렬(`sort`, 후보 목록), 활성 필터(체크된 항목, aria 칩, URL 매개변수), 페이지네이션(`current_page`/`total_pages`/`has_next`/`has_prev`/`next`·`prev` 요소 ID/페이지 ID 목록), 페이지당 표시 수, 전체 결과 수("총 N개", "전체 (N)", "N results" 등), 검색어, 배송비 포함·무료배송 같은 토글의 현재 상태를 돌려줍니다. `fingerprint`를 다음 호출의 `compare`에 넘기면 페이지 이동 후 정렬/필터 유지 여부를 `preserved`/`differences`로 검증합니다.

### P1 — 자동화 효율 및 기능 개선

- **P1-1 페이지 내부 구조화 질의**: `query` 명령을 추가했습니다. CSS 선택자(및 `scope` 요소 ID/선택자, `frame`)로 Chromium 안에서 요소를 고르고 `id`/`tag`/`text`/`name`/`role`/`href`/`src`/`value`/`visible`/`classes`/`bounds`/`price`/`number`와 지정 속성(`attributes`)만 돌려줍니다. 텍스트 조건(`text`+`match`), 숨김 제외, 중복 제거, 페이지네이션, 예산을 지원합니다. 임의 JavaScript 실행 명령은 없으며 선택자·필드·속성 이름은 데이터로만 전달되고 쿠키·저장소·네트워크 API에는 접근하지 않습니다.
- **P1-2 Find 검색 및 출력 최적화**: `find`에 `fields`, `match=contains|exact|regex|prefix`, 문자열 배열 + `operator=and|or`, 검색 대상 필드 `in`, `sort_by=document|name|role|position`, `dedupe`(true 또는 필드 배열, `duplicates_removed`), `limit`/`total`/`omitted`/`next_offset`를 추가하고 기본 compact 출력을 적용했습니다.
- **P1-3 검색 결과의 계층 문맥 제공**: `find`에 `with_parent=n`(조상 n개), `with_children=n`(깊이 n 자식 트리), `child_roles`(지정 역할의 후손만 평면 목록으로, 관련 없는 후손 제외)를 추가했습니다.
- **P1-4 SPA 비동기 로딩 완료 감지**: `wait`와 `action`/`navigate`의 `expect`가 같은 조건 문법을 씁니다. `network_idle_ms`(진행 중 요청 없음 + 조용한 시간), `dom_stable_ms`(DOM 변이 없음), `selector`(+`state`), `value`(선택/입력 값), `url_changed`, `title`을 추가했고 요소/텍스트/URL/다운로드 조건도 유지됩니다. 시간 초과 시 `WAIT_TIMEOUT`의 `details.unmet`/`met`에 조건별 기대값과 실제값을 돌려줍니다. 모든 관측 응답의 `page.stability`에 진행 중 요청 수, 네트워크/DOM 조용 시간을 제공합니다.
- **P1-5 요소 ID 단축**: Agent용 ID를 `t1e12`(탭 ID + e + 순번) 형식으로 바꿨습니다. 탭마다 독립 순번이고 같은 탭의 프레임은 순번을 공유하므로 탭·프레임 사이에서 충돌하지 않으며, 같은 DOM 노드는 세션 동안 같은 ID를 유지합니다. 긴 내부 ID는 `detail.element.internal_id`로만 노출하고, 이전 형식의 긴 ID도 모든 명령에서 계속 받아들입니다. 문서 교체 시 이전 문서의 매핑을 정리합니다.
- **P1-6 응답별 토큰 예산 제어**: 모든 관측 명령에 `max_bytes`/`max_elements`/`max_tokens`를 추가했습니다. 응답의 `budget`에 실제 바이트 수, 요소 수, 예상 토큰 수, 사용한 tokenizer(`heuristic-v1`), 적용한 한도, 잘림 여부/원인/다음 offset을 돌려줍니다. 세션 기본 바이트 상한은 `--max-bytes`(기본 500,000, 0이면 무제한)이며 요청값이 우선합니다. `capabilities`에 `tokenizer`/`default_max_bytes`를 표시합니다.

### P2 — 실행 안정성 및 운영 관리

- **P2-1 안전한 Chromium Sandbox 실행**: `--install-sandbox`가 Ubuntu 24.04+의 AppArmor user namespace 제한 환경에서 번들 Chromium 전용 `userns` 허용 정책(`/etc/apparmor.d/aribrowser-chromium`)을 설치·적재하고 호출 사용자 권한으로 실제 실행을 검증합니다. `--sandbox-method suid`로 setuid 샌드박스 도우미 설치도 지원하며 실행 시 `CHROME_DEVEL_SANDBOX`로 전달합니다. root 실행(`SANDBOX_ROOT_USER`)과 AppArmor 제한(`SANDBOX_USERNS_RESTRICTED`)을 구분하고, 어떤 경로에서도 `--no-sandbox`를 자동으로 사용하지 않습니다. 세션 중 실행 실패도 같은 코드와 `details.fix`로 보고합니다.
- **P2-2 브라우저 프로세스 및 세션 정리**: `close`/`profile.close`가 Chromium 주 프로세스와 자손(렌더러/GPU/유틸리티)의 종료를 확인하고 종료/강제 종료/잔여 프로세스 수와 잠금 해제 여부를 돌려줍니다. 프로필을 열 때 소유 프로세스가 없는 잔여 잠금을 복구하고(`profile_lock_recovered` 이벤트), 사용 중인 잠금은 `PROFILE_LOCKED` 오류로 알립니다. `--cleanup`/`session.cleanup`이 이전 세션의 잔여 프로세스와 잠금을 정리하며 현재 세션의 프로세스는 제외합니다. 프로세스 추적에 psutil을 사용합니다.
- **P2-3 실행 환경 자동 진단**: `--doctor`/`doctor` 명령이 AriBrowser·Chromium(실제 실행 경로, headless shell, 버전)·실행 사용자/UID/root 여부·DISPLAY/WAYLAND_DISPLAY/XAUTHORITY·AppArmor 활성/제한 값/정책 파일·적재 상태·user namespace 생성 가능 여부(AppArmor `unprivileged_userns` 전환 감지)·setuid 도우미·실제 샌드박스 실행 검사(렌더러 seccomp 근거)·잔여 프로세스/잠금을 단계별 `checks[].code`와 `summary.error_code`/`fix`로 돌려줍니다. `--no-launch`로 실행 검사를 생략할 수 있습니다.
- **P2-4 브라우저 업데이트 후 Sandbox 유지**: 정책은 실행 파일 절대 경로 대신 브라우저 루트 아래 glob(`**/{chrome,chrome-headless-shell}`)에 붙어 같은 설치 디렉터리의 Chromium 리비전 변경에 영향을 받지 않습니다. 정책 머리글의 `aribrowser-root`와 현재 경로를 비교해 `SANDBOX_PROFILE_STALE`을 감지하고, 재설치는 같은 이름의 정책을 덮어써 오래된 정책과 충돌하지 않으며 `--uninstall-sandbox`로 제거할 수 있습니다. 설치 뒤 `--doctor`/`--self-test`로 정상 실행을 확인합니다.

### 그 밖의 변경

- 수집기(collector.js)가 기본값과 같은 상태·빈 필드를 생략한 압축 형식을 JSON 문자열로 돌려주고 Python이 전체 스키마로 되돌립니다. 출력 요소 상한(20,000)과 숨김 서브트리 상한(5,000 노드)을 두고 `DOM_LIMIT`/`HIDDEN_LIMIT` 경고로 알립니다. `bounds`는 정수 픽셀입니다.
- 공용 ID 레지스트리(registry.js)를 collector/query/pagestate가 공유해 어느 명령으로 얻은 요소든 같은 ID로 조작할 수 있습니다. 활동 추적기(activity.js)가 DOM 변이 시각과 조작 이벤트 탐지를 담당합니다.
- `capabilities`가 요소 필드 목록, 상태 기본값, 출력 형식, 대기 조건, 진단 코드, 샌드박스 여부를 추가로 돌려줍니다. 알 수 없는 매개변수 오류에 허용 목록을 포함합니다.
- 빌드 스크립트가 번들 바이너리로 `--doctor`와 `--self-test`를 실행하고, 샌드박스를 지원하지 않는 호스트에서만 `--no-sandbox`로 재검증하며 그 사실을 출력합니다. GitHub Actions에 Ubuntu 24.04 샌드박스 정책 설치 검증 작업을 추가했습니다.
- 테스트 사이트에 상품 검색 결과 페이지(반복 레코드, 정렬 select/탭, 필터 토글, 페이지네이션, 전체 건수, 비동기 재정렬)를 추가하고 P0~P2 요구사항별 통합 테스트와 CLI 진단/정리 테스트를 추가했습니다.
- 문서: `docs/PROTOCOL.md`(새 명령/옵션/compact 규칙/예산/검증), `docs/FEATURES.md`(요구사항별 구현과 경계), `docs/SANDBOX.md`(샌드박스 설치·진단·정리 가이드), README를 갱신했습니다. psutil을 서드파티 고지에 추가했습니다.

### 호환성

- 기본 출력이 요약/텍스트/compact로 바뀌었습니다. 0.0.1과 같은 응답이 필요하면 `mode=full`, `format=both`, `compact=false`, `max_bytes=0`을 지정하세요.
- 요소 ID 형식이 바뀌었지만 이전 형식의 긴 ID도 계속 받아들입니다.
- `close` 응답은 `closed=true`와 함께 프로세스/잠금 정리 보고를 포함합니다.

## 0.0.1 — 2026-10-11

AriBrowser 최초 릴리스. AI Agent가 웹페이지를 실제 Chromium으로 실행하고 텍스트로 관측·조작하는 브라우저를 구현했습니다.

### 브라우저와 세션

- Playwright/Chromium 실행 엔진, HTML/CSS/JavaScript 및 SPA·비동기 콘텐츠 처리를 추가했습니다.
- 표준입출력 JSONL 제어 프로토콜, 일회성 `--snapshot`, `--self-test`, 버전/도움말 CLI를 추가했습니다.
- 프로필별 영구 저장소, 쿠키/로그인 세션 격리, 탭 생성·전환·종료, 새 창/팝업 감지를 구현했습니다.
- 방문 기록 앞뒤 이동, 새로고침, URL/제목/HTTP 상태, 데스크톱/모바일 화면 크기 설정을 추가했습니다.
- 교차 출처 iframe과 open Shadow DOM 수집·입력, 스크롤 컨테이너·가상 목록 재관측을 지원합니다.

### 의미 화면·조회·조작

- DOM·계산된 스타일/레이아웃과 Chromium 접근성 트리를 결합한 JSON/텍스트 화면을 추가했습니다.
- 제목/본문/링크/버튼/메뉴/영역/리스트/표/폼/탭/모달/알림/오류/이미지/미디어를 의미 요소로 표현합니다.
- 노드 정체성 기반 ID와 부모/프레임/그룹 관계, 레이블/설명, 값·선택·활성화·읽기 전용·펼침·가림·화면 위치·유효성 상태를 제공합니다.
- 텍스트/역할/영역/조작 가능 요소 검색, 상세·주변 문맥, 표 행/열 선택 조회를 추가했습니다.
- 링크/버튼 클릭, 입력·삭제·키보드, 체크/라디오/드롭다운, 날짜/시간, 폼 제출, 호버·더블 클릭·드래그, 모달 닫기, 좌표 예외 조작을 추가했습니다.
- 전체·화면·주요 콘텐츠·상호작용·영역·변경분 출력과 페이지네이션/생략 표시를 구현했습니다.
- 구조 영역의 이름에 후손 텍스트가 반복되지 않도록 출력량을 줄이고, macOS와 Windows/Linux의 네이티브 키보드 차이를 검증했습니다.
- 비밀번호와 기본 쿠키 값은 가리고, 웹 원문은 이스케이프된 데이터로 출력합니다.

### 변경분·파일·오류

- 전체 관측 기준선과 출력용 부분 뷰를 분리하여 생략을 DOM 삭제로 오인하지 않도록 했습니다.
- 요소 추가/제거/속성 변경, URL/페이지 상태 변화와 순번 기반 비동기 이벤트를 제공합니다.
- 로딩, 리디렉션, 새 탭/창, 브라우저 대화상자, 다운로드의 시작/완료/실패를 기록합니다.
- 파일 input 업로드와 충돌 없는 로컬 다운로드 저장, 다운로드/자원 상태 조회를 추가했습니다.
- 네트워크/HTTP 404·500/인증/누락·변경 요소/조작 불가/충돌/응답 제한 시간을 구분합니다.
- 조건부 wait, 입력 검증, 유한 이벤트·자원 버퍼와 잘못된 요청 이후 연결 복구를 구현했습니다.

### 시각화·자원·배포

- 기본 이미지 없는 화면, 이미지 메타데이터, Canvas/WebGL/차트/지도 시각 확인 표시를 추가했습니다.
- 특정 요소·영역·뷰포트·전체 페이지의 PNG 렌더와 preview/normal/original 해상도, 선택적 base64를 지원합니다.
- 원본 래스터 이미지 픽셀 반환, 선택적 로컬 Tesseract OCR, 비전 Agent용 로컬 이미지 출력을 추가했습니다.
- 이미지·미디어 네트워크 요청 보류, 요청 범위 이미지 로딩, 자동 재생 억제, 장식/배경 출력 생략과 자원 GET 재요청을 구현했습니다.
- Python 패키징과 Chromium 포함 Linux x64, Windows x64, macOS ARM64/Intel 네이티브 빌드 및 SHA-256 생성을 추가했습니다.
- macOS Chromium 앱 번들의 원본 디렉터리/심볼릭 링크/제공자 서명을 보존하여 네이티브 패키징 오류를 해결했습니다.
- GitHub Actions의 OS별 실제 Chromium 통합 테스트, 바이너리 실행 검증 및 버전 릴리스 게시를 구성했습니다.
- 모듈·클래스·함수·핵심 변수와 비자명한 처리의 목적을 한국어 주석으로 설명하고, 사용법·프로토콜·기능 경계·서드파티 라이선스 문서를 작성했습니다.

### v0.0.1 범위

closed Shadow DOM 직접 조작, OCR 엔진/언어팩 번들, 내장 비전 모델, macOS 공증/Windows 코드 서명은 포함하지 않습니다. 이미지 보류와 서비스 워커 사용의 호환성 선택, DOM/AX 수집 한도, 가림 추정 등은 `docs/FEATURES.md`에 명시했습니다.
