# AriBrowser JSONL v1

UTF-8 JSON 객체 하나를 한 줄에 전송합니다. 요청은 순차 실행되며 `id`가 응답에 그대로 전달됩니다. 브라우저는 첫 프로필/탐색 요청에서 시작됩니다. EOF 또는 `close`에서 정상 종료하며 세션 저장소를 보존합니다. GUI 대신 로컬 프로세스 파이프를 연결하는 Agent용 브라우저입니다.

```json
{"id":"request-1","command":"snapshot","params":{"mode":"viewport","limit":100}}
```

성공은 `{"id":...,"ok":true,"result":...}`, 실패는 `{"id":...,"ok":false,"error":{"code":...,"message":...,"details":...}}`입니다. 잘못된 JSON이나 명령 이후에도 연결은 유지됩니다. 작업 실패는 롤백을 뜻하지 않습니다. `snapshot`/`events`로 실제 결과를 확인한 뒤 재시도하세요.

## 명령

`tab`을 받는 명령에서 생략하면 현재 활성 탭을 사용합니다. `profile`은 프로필 이름입니다. ID는 응답에서 받은 값을 그대로 사용하며 CSS 선택자/좌표로 해석하지 않습니다.

| 명령 | params | 동작 |
| --- | --- | --- |
| `capabilities` | 없음 | 버전, 명령/조작 목록, OCR 가능 여부 |
| `profile.create` | `name=default`, `viewport={width,height}`, `mobile=false`, `device_scale_factor=1`, `media_policy=defer` | 영구 프로필 열기; 이름은 영숫자/밑줄/하이픈 1~64자 |
| `profile.list` | 없음 | 열린 프로필과 자원 정책 |
| `profile.close` | `name` | 프로필 종료; 데이터 보존 |
| `tab.new` | `profile?`, `url?` | 새 탭을 만들고 활성화 |
| `tab.list` | 없음 | ID, 프로필, URL, 제목, 활성/로딩/충돌 상태 |
| `tab.switch` | `tab` | 활성 탭 전환 및 전체 화면 |
| `tab.close` | `tab?` | 탭 종료 및 남은 탭 목록 |
| `navigate` | `url`, `tab?`, `mode=changes`, `settle_ms=150` | 탐색 및 변경분; `mode=full`로 첫 화면 읽기 |
| `snapshot` | `tab?`, `mode=full`, `region?`, `offset=0`, `limit=500`, `include_hidden=false` | 전체/부분/변경분 관측 |
| `find` | `tab?`, `text?`, `role?`, `region?`, `actionable?`, `include_hidden=true`, `offset=0`, `limit=100` | 부분 문자열/역할/영역 검색; role은 문자열 또는 배열 |
| `detail` | `element`, `tab?`, `radius=2` | 상세, 조상, 직계 자식, 앞뒤 문맥 |
| `table` | `element`, `tab?`, `rows?`, `columns?` | 표의 선택한 행/열; 인덱스는 0부터 시작하며 헤더 행 포함 |
| `action` | `action`, `tab?`, 아래 조작 매개변수 | 입력 후 변경분 |
| `render` | 아래 렌더 매개변수 | 요청 범위 PNG 및 메타데이터 |
| `media.load` | `tab?`, `element?` | 해당 요소와 하위 이미지 로딩; 요소 생략 시 주 문서 |
| `resources` | `tab?`, `status?`, `offset=0`, `limit=100` | URL, 자원 종류, 용도, 로딩 상태, HTTP 상태 |
| `resource.request` | `resource` | 관측한 HTTP(S) 자원을 GET 재요청해 파일로 저장 |
| `events` | `since=0` | 이후 이벤트와 다음 cursor; 최대 최근 2,000개 |
| `downloads` | `tab?` | 다운로드 시작/완료/실패, 저장 경로, 파일 크기 |
| `dialog.policy` | `tab?`, `action=dismiss 또는 accept`, `text?` | 다음 alert/confirm/prompt의 즉시 응답 정책 |
| `cookies.list` | `profile?`, `include_values=false` | 기본 값 가림; 쿠키 유무로 로그인을 단정하지 않음 |
| `cookies.set` | `profile?`, `cookies` | Chromium 쿠키 객체 배열 추가 |
| `cookies.clear` | `profile?` | 지정 프로필 쿠키 제거 |
| `wait` | `tab?`, `element?`, `text?`, `state=visible`, `url?`, `download?`, `timeout_ms?` | 모든 지정 조건 충족 대기; 제한 시간 1~30,000ms |
| `close` | 없음 | 모든 프로필을 저장하고 연결 종료 |

`snapshot.mode`: `full`, `viewport`, `main`, `interactive`, `region`, `changes`. `region` 모드는 `region` ID 필수입니다. 페이지네이션 범위는 offset ≥ 0, limit 1~10,000입니다. `omitted.total/filtered/remaining/next_offset/available`로 생략을 알립니다. 다음 offset을 조회하거나 `region`/`detail`로 펼칩니다. `changes`는 마지막 **snapshot 또는 조작 응답** 이후 전체 문서 변화만 반환합니다. `find`/`detail`/`table`은 변경분 기준선을 소비하지 않습니다. 전체/부분 화면 응답에는 전체 변화 목록을 중복 삽입하지 않습니다.

`wait.state`: `visible`, `hidden`, `attached`, `detached`, `enabled`. `url`은 URL 부분 문자열입니다. `download`는 다운로드 ID 또는 `any`입니다. 존재하지 않는 텍스트를 `hidden`/`detached`로 기다리면 즉시 성공합니다.

## 요소와 상태

`id`, `parent`, `frame`, `role`, `tag`, `name`, `text`, `value`, `labels`, `description`, `validation_message`, `href`, `input_type`, `groups`, `bounds`, `actions`, `state`, `style`, `media`, `accessibility_source`를 제공합니다.

- `state`: `visible`, `in_viewport`, `occluded`, `disabled`, `readonly`, `selected`, `checked`(true/false/`mixed`), `expanded`(true/false/null), `invalid`, `required`, `focused`, `accessibility_hidden`, `actionable`.
- `bounds`: 페이지의 **현재 뷰포트 기준 CSS 픽셀** x/y/width/height. iframe 위치를 합산합니다. 가림은 요소의 보이는 중앙 지점 hit-test 추정이며 픽셀 전체 분석은 아닙니다. 실제 입력은 Chromium/Playwright의 조작 가능 검사로 다시 검증합니다.
- `style`: 강조(strong/normal), 의미(warning/normal), 표시(visible/hidden), 위치(modal/fixed/flow). CSS 전체를 출력하지 않습니다.
- `groups`: 가장 가까운 form/table/group/list의 ID. `parent`를 통해 다른 영역도 탐색할 수 있습니다.
- `media`: alt, source, size, loaded, rendered=false, needs_visual, context. 보류된 원본 크기가 아직 알려지지 않았다면 계산된 표시 크기를 사용합니다.
- `name`은 최대 4,000자, `text`는 최대 8,000자이며 잘림은 `text_truncated`로 명시합니다. 네이티브 AX 연결은 `chromium-ax`, 숨김 등 DOM/ARIA 보완은 `dom-aria`입니다.

동일 DOM 노드는 텍스트/속성 변경 및 재배치 후에도 ID를 유지합니다. 새 문서·새 노드에는 다른 ID를 부여합니다. 가상 목록 프레임워크가 하나의 DOM 노드에 다른 항목을 표시하면 ID는 DOM 정체성을 나타내며 항목 텍스트/값 변경이 반환됩니다.

## 조작

공통 `mode=changes`, `settle_ms=150`(0~5,000ms), `tab?`을 지원합니다.

| action | 추가 매개변수 | 설명 |
| --- | --- | --- |
| `click`, `open_link` | `element` | 버튼/링크/메뉴/탭 클릭 |
| `double_click`, `hover` | `element` | 두 번 클릭/마우스 오버 |
| `fill`, `type` | `element`, `text` | 전체 값 교체/키보드 입력; 날짜 `YYYY-MM-DD`, 시간 `HH:mm` |
| `clear` | `element` | 입력 지우기 |
| `check`, `uncheck` | `element` | 체크박스/라디오/ARIA 체크 상태 |
| `select` | `element`, `values` | 네이티브 select의 value 또는 value 배열 |
| `upload` | `element`, `files` | 로컬 파일 경로 배열; 빈 배열이면 선택 제거 |
| `submit` | `element` | 해당 폼/소속 폼을 HTML 검증 후 제출 |
| `close_modal` | `element` | HTML dialog.close 또는 사용자 모달 Escape |
| `scroll_into_view` | `element` | 해당 요소로 스크롤 |
| `scroll` | `element?`, `x=0`, `y=600` | 스크롤 컨테이너 또는 페이지 휠 이동 |
| `press` | `key`, `element?` | `Enter`, `Escape`, `Control+A` 등 |
| `drag_drop` | `element`, `target` | ID 대상 사이 실제 포인터 드래그 |
| `back`, `forward`, `reload` | 없음 | 방문 기록/새로고침 |
| `coordinate_click` | `x`, `y` | 예외적 CSS 픽셀 좌표 클릭 |

사용자 정의 드롭다운은 `click` 후 항목 ID를 다시 조회해 선택합니다. Escape를 처리하지 않는 사용자 정의 모달은 닫기 버튼을 클릭하세요. 임의 JavaScript 평가/강제 클릭 명령은 제공하지 않습니다.

키보드는 해당 OS의 실제 단축키 의미를 따릅니다. 예를 들어 macOS 입력 줄 끝 이동은 `Meta+ArrowRight`, Windows/Linux는 `End`입니다. 전체 선택에는 플랫폼에 맞게 해석되는 `ControlOrMeta+A`를 사용할 수 있습니다.

## 렌더

```json
{"id":10,"command":"render","params":{"element":"ACTUAL_IMAGE_ID","level":"original","ocr":true,"ocr_language":"kor+eng"}}
{"id":11,"command":"render","params":{"target":"region","clip":{"x":0,"y":0,"width":640,"height":480},"level":"preview"}}
```

- `target`: `element`(element 필수), `viewport`(기본), `page`(스크롤 전체), `region`(clip 필수).
- `level`: `preview`(긴 변 최대 480px), `normal`(CSS 해상도), `original`(이미지는 지원되는 원본 파일 픽셀, 그 외 장치 해상도).
- `load_media=true`: 요청 범위의 이미지 URL을 허용하고 재로딩. `false`이면 현재 로딩 상태 그대로 촬영.
- `base64=false`: 기본 PNG 파일 경로만 반환. true일 때 PNG 바이트의 base64도 반환.
- `ocr=false`, `ocr_language=eng`: 로컬 Tesseract 추출. 미설치 시 `ocr.status=unavailable`, 언어팩 오류 시 failed.
- 응답: path/mime_type/bytes/size/target/level/rendered/description 및 선택적 base64/ocr. description은 페이지 메타데이터이며 AI가 추론한 이미지 설명이 아닙니다. 필요하면 제어 Agent가 PNG를 비전 모델에 입력합니다.

## 이벤트와 오류

이벤트는 seq/time/type과 tab/profile 등의 문맥을 갖습니다. `loading_started`, `dom_ready`, `loading_finished`, `navigation`, `frame_navigation`, `redirect`, `tab_created`, `tab_closed`, `popup_created`, `dialog_opened`, `dialog_closed`, `download_started`, `download_completed`, `download_failed`, `network_failed`, `not_found`, `authentication_required`, `server_error`, `http_error`, `script_error`, `browser_crashed`, `profile_closed`, `background_error`를 제공합니다. 버퍼 초과는 `events_omitted=true`로 표시합니다. 조작 응답 이후에 끝난 비동기 작업은 `events` 또는 다음 `snapshot`/`wait`로 받습니다.

주요 오류 코드는 `INVALID_JSON`, `INVALID_REQUEST`, `INVALID_ARGUMENT`, `UNKNOWN_COMMAND`, `TAB_NOT_FOUND`, `PROFILE_NOT_FOUND`, `PROFILE_EXISTS`, `ELEMENT_NOT_FOUND`, `STALE_ELEMENT`, `ELEMENT_NOT_ACTIONABLE`, `ACTION_FAILED`, `NETWORK_FAILED`, `BROWSER_UNAVAILABLE`, `BROWSER_CRASHED`, `BROWSER_ERROR`, `PAGE_UNRESPONSIVE`, `WAIT_TIMEOUT`, `FILE_NOT_FOUND`, `RESOURCE_NOT_FOUND`, `RESOURCE_FAILED`, `IO_ERROR`, `SESSION_CLOSED`입니다. HTTP 404/500은 렌더 가능한 문서이므로 탐색은 성공 응답이며 page.http_status와 이벤트에 오류를 표시합니다.
