# v0.1.0 기능과 경계

| 요구 영역 | 구현 |
| --- | --- |
| Chromium 실행 | HTML/CSS/JavaScript, SPA, 쿠키, 로컬/세션 저장소, IndexedDB, 비동기 요청, 실제 레이아웃, 샌드박스 활성 실행 |
| 텍스트 화면 | 주요 영역, 제목/본문/링크/버튼/리스트/표/폼/탭/모달/알림/오류/이미지/영상; Chromium AX 보강; 요약/텍스트/요소/둘 다 형식 |
| 상태 | 값, 체크/선택/읽기 전용/펼침/비활성화/표시/화면 내외/가림/조작 가능 여부, 레이블/설명/그룹/검증 오류; compact 출력은 기본값 생략 |
| 식별과 탐색 | 짧은 노드 정체성 ID(`t1e12`), 텍스트/역할/영역 검색(정확/정규식/AND·OR/정렬/중복 제거), 상세/문맥(조상·자식 깊이·역할 필터), 표 행/열 |
| 브라우저 내부 질의 | CSS 선택자 질의(필드/속성 선택), 반복 레코드 추출(자동 감지/필드 명세/누락 표시/중복 제거), 정렬·필터·페이지 상태 감지 |
| 조작 | 클릭, 입력/삭제, 체크/선택, 날짜/시간, 폼 제출, 메뉴/탭/모달, 스크롤, 키보드, 호버, 더블 클릭, 드래그, 좌표 예외; 전달/수신/기대 상태 검증 |
| 변경분·대기 | 추가/제거/속성/값 변화, URL/탐색/로딩/새 창/다운로드 이벤트; 요소/텍스트/선택자/값/URL/제목/네트워크 안정/DOM 안정 대기 |
| 응답 제어 | 전체/뷰포트/주요 영역/상호작용/특정 영역/상세/변경분/요약, offset/limit, 바이트/요소/토큰 예산, 실제 크기와 추정 토큰 보고 |
| 선택적 이미지 | 요소/이미지/차트/지도/영역/화면/전체 페이지, preview/normal/original, 로컬 OCR 연동, 메타데이터 |
| 자원 절약 | 이미지·미디어 요청/디코딩 보류, 요청된 URL 허용, 자동 재생 억제, 장식 이미지/배경 출력 생략, 자원 GET 재요청/상태 |
| 탐색 상태 | 탭 생성/종료/전환, 팝업 감지, 뒤로/앞으로/새로고침, 영구 프로필별 세션 격리, 쿠키 관리 |
| 파일 | input 업로드, 첨부 파일 다운로드 자동 보관, 완료/실패 조회 |
| 운영 | 실행 환경 진단(`--doctor`), AppArmor/setuid 샌드박스 설치·검증·갱신, 프로세스/잠금 정리 보고, 잔여 세션 정리 |
| 상태와 오류 | HTTP/네트워크/인증/요소/다운로드/팝업/충돌/응답 제한 시간/기대 미달/샌드박스·권한·의존성 실패를 분리 |

## 요구사항별 구현 요약 (P0~P2)

| 번호 | 항목 | 구현 | 경계 |
| --- | --- | --- | --- |
| P0-1 | Navigation 응답 크기 제한 | `navigate` 기본 `mode=summary`, `max_regions`/`max_headings`, `omitted.elements`와 `omitted.next`, 모든 모드에 예산 적용 | 요약의 영역/제목은 보이는 랜드마크·heading 기준이며 사이트 고유 구획(div)은 `find`/`extract`로 조회 |
| P0-2 | Snapshot 경량화 | `format=summary/text/elements/both`, 기본 `text`, `fields`, `compact`(빈 값·기본 상태·중복 텍스트 생략), 분할 조회 | compact 규칙은 PROTOCOL.md에 명시; 전체 형태는 `compact=false` |
| P0-3 | 반복 레코드 구조화 추출 | `extract`: 자동 감지/`item` 선택자, 자동 필드(title/url/price/shipping/image/text) 또는 필드 명세, 레코드별 범위 한정, `missing` 원인, `dedupe` | 자동 감지는 형제 서명 휴리스틱이므로 `detection.candidates`를 확인하고 필요하면 `item`을 지정 |
| P0-4 | Action 실행 결과 검증 | `verification.action_dispatched/event_received/expected_state_reached`, `expect` 미달 시 `EXPECTATION_NOT_MET`, `delta.url`, 변경분 | 이벤트 수신은 캡처 리스너 기준이며 페이지 스크립트 내부 처리 결과까지 보장하지 않음; 기대 상태는 `expect`로 명시 |
| P0-5 | 정렬·필터·페이지 상태 검증 | `page.state`: 정렬(URL/select/활성 컨트롤), 필터/토글, 페이지 번호/전체/다음·이전 ID, 페이지당 수, 전체 건수, 검색어, `fingerprint`+`compare` | 한국어/영어 키워드 휴리스틱; 감지 실패 시 `checks`가 false이며 `find`/`query`로 보완 |
| P1-1 | 페이지 내부 구조화 질의 | `query`: 선택자/범위/필드/속성/텍스트 조건/중복 제거, 브라우저 내부 필터링, 임의 JS 없음, 쿠키/저장소/네트워크 비노출 | 선택자는 주 프레임 기본, `frame`으로 iframe 지정 |
| P1-2 | Find 검색 및 출력 최적화 | `fields`, `match`(contains/exact/regex/prefix), `operator`(and/or), `in`, `sort_by`, `dedupe`, `limit`, `total`/`omitted`/`next_offset` | — |
| P1-3 | 검색 결과 계층 문맥 | `with_parent`, `with_children`, `child_roles`(역할 필터 평면 목록), `children_omitted` | 자식은 요소당 50개 |
| P1-4 | SPA 비동기 로딩 감지 | `wait`/`expect`: `network_idle_ms`, `dom_stable_ms`, `selector`, `value`, `url_changed`, `title`; `page.stability`; 시간 초과 시 `unmet`/`met` | 네트워크 안정은 Playwright가 관측한 요청 기준(WebSocket 유지 연결은 요청 완료로 간주) |
| P1-5 | 요소 ID 단축 | `t1e12` 형식, 탭별 순번, 프레임 공유, 세션 내 안정, `detail.internal_id`, 긴 ID 호환 | 문서 교체 시 이전 ID는 `STALE_ELEMENT` |
| P1-6 | 응답별 토큰 예산 | `max_bytes`/`max_elements`/`max_tokens`, `budget{bytes,elements,estimated_tokens,tokenizer,truncated,next_offset}`, `--max-bytes` 세션 기본 | 토큰은 문자 종류 가중치 근사(heuristic-v1) |
| P2-1 | 안전한 Chromium Sandbox 실행 | `--install-sandbox`(AppArmor userns 정책/setuid 도우미), 설치 후 호출 사용자 권한 검증, `--no-sandbox` 자동 사용 없음, root/AppArmor 구분 | root 권한 1회 필요 |
| P2-2 | 프로세스 및 세션 정리 | `close`/`profile.close` 프로세스 종료·잔여 수·잠금 해제 보고, 잔여 잠금 자동 복구, `PROFILE_LOCKED`, `--cleanup`/`session.cleanup` | Windows는 lockfile 열기 가능 여부로 잔여 판단 |
| P2-3 | 실행 환경 자동 진단 | `--doctor`/`doctor`: 버전·Chromium 경로/버전·사용자/UID·DISPLAY/XAUTHORITY·AppArmor·userns·setuid·샌드박스 실행·seccomp 근거·프로세스·잠금, 단계별 코드 | — |
| P2-4 | 업데이트 후 Sandbox 유지 | 브라우저 루트 glob 정책, `aribrowser-root` 머리글로 경로 비교, `SANDBOX_PROFILE_STALE` 감지, 같은 이름 덮어쓰기, `--uninstall-sandbox`, 설치 후 `--doctor`/`--self-test` | 설치 디렉터리를 옮기면 재설치 필요 |

## Chromium과 관측 범위

교차 출처 iframe은 각각의 Playwright 프레임에서 수집·조작합니다. open Shadow DOM을 순회하며 closed Shadow DOM 내부 노드는 현재 직접 ID 조작 대상으로 노출하지 않습니다. 브라우저 보안·인증·자동화 차단 정책을 우회하지 않습니다. CAPTCHA 해결, 확장 프로그램 UI, DRM 영상 재생은 구현 범위에 포함하지 않습니다.

무한 스크롤/가상 목록은 Chromium에서 실행됩니다. 스크롤 후 새로 만들어진 DOM을 읽으며 서버에 아직 요청하지 않은 모든 항목을 추측하지 않습니다. `aria-setsize/aria-posinset/aria-rowindex/aria-colindex`와 생략 안내를 제공하고, 해당 컨테이너를 `scroll`한 뒤 재조회합니다.

한 프레임당 DOM 방문은 50,000개, AX 보강은 5,000개로 제한하고 상한 도달을 warnings에 표시합니다. 가림 판단은 중앙점 기반 추정입니다. iframe의 부모 영역에서 발생하는 복잡한 가림이나 CSS 변환은 실제 클릭 검증 결과를 우선합니다. `query`/`extract`/`page.state`는 관측 트리를 만들지 않고 해당 프레임 안에서 바로 실행되므로 대형 페이지에서도 응답이 작고 빠릅니다.

`defer`는 **네트워크 image/media 종류**를 차단합니다. script/CSS/font/XHR/fetch는 페이지 동작을 위해 허용하므로 JS가 fetch로 받은 이미지, data/blob URL, 인라인 SVG/Canvas의 디코딩/그리기까지 완전히 억제하지는 않습니다. 서비스 워커를 통한 우회 요청은 defer 프로필에서 차단합니다. 서비스 워커가 필요한 사이트는 새 프로필을 `media_policy=allow`로 시작하세요.

프로필 데이터는 Chromium 디스크 저장 규칙을 따릅니다. 영속 쿠키와 로컬 저장소는 재시작 시 유지되며 세션 쿠키 만료·서버 로그아웃 정책을 바꾸지 않습니다. 인증 상태는 HTTP 401/403이나 비밀번호 폼의 관측 결과만 표시합니다. 동일 프로필을 두 프로세스에서 동시에 열면 `PROFILE_LOCKED`가 발생하므로 Agent마다 다른 data-dir/profile을 사용하세요.

## 검증 정확성에 대한 주의

`verification.event_received`는 페이지가 DOM 이벤트를 받았다는 사실만 뜻합니다. 정렬 변경처럼 결과가 비동기로 반영되는 작업은 `expect`에 URL/텍스트/선택자/값/네트워크 안정 조건을 지정해 `expected_state_reached`로 확인하고, `page.state`의 `fingerprint`를 `compare`로 넘겨 페이지 이동 후 조건 유지 여부를 검증하세요. `page.state`와 `extract`의 자동 감지는 휴리스틱이므로 `checks`/`detection`을 보고 필요하면 `item`/`fields`/`query`로 명시적 선택자를 지정합니다.

## 이미지 이해

Canvas/WebGL/영상/SVG와 이름상 차트·지도는 `needs_visual`로 시각 확인 필요성을 표시합니다. 원본 래스터 이미지는 원본 픽셀로 렌더하고, SVG/요소/페이지는 Chromium 장치 해상도를 사용합니다. OCR은 설치된 Tesseract와 언어팩으로 수행합니다. 비전 모델 자격 증명은 요구하지 않으며 이미지 의미 해석은 호출 Agent에 맡깁니다.

## 검증과 배포

실제 로컬 HTTP 사이트를 사용하는 통합 테스트가 의미/ID/변경분, 상태/폼, Shadow DOM/교차 출처 iframe, 스크롤/가상 목록, 모달/대화상자/팝업, 포인터/파일, 이미지 보류/렌더, HTTP 오류/방문 기록, 프로필 격리/복원, 잘못된 요청 복구와 P0~P2 요구사항(요약 탐색, 형식/예산, 레코드 추출, 조작 검증, 페이지 상태, 내부 질의, 검색 최적화/문맥, SPA 대기, 짧은 ID, 예산 보고, 프로세스/잠금 정리, 진단)을 검증합니다. 네이티브 빌드에 포함된 Chromium을 `--self-test`/`--doctor`로 다시 실행합니다. Ubuntu 24.04의 AppArmor userns 제한 환경에서 `--install-sandbox` 후 샌드박스가 켜진 실행을 검증했습니다. CI 결과는 [Actions](https://github.com/zzabisagent-lab/AriBrowser/actions)에서 확인할 수 있습니다.

배포 바이너리에는 Chromium·Playwright·Python 런타임이 포함되어 크기가 큽니다. Linux 시스템 공유 라이브러리와 선택적 Tesseract는 번들에 포함하지 않습니다. macOS/Windows 상용 배포 서명 자격 증명은 사용하지 않습니다.
