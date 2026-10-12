# AriBrowser JSONL v1 (0.1.0)

UTF-8 JSON 객체 하나를 한 줄에 전송합니다. 요청은 순차 실행되며 `id`가 응답에 그대로 전달됩니다. 브라우저는 첫 프로필/탐색 요청에서 시작됩니다. EOF 또는 `close`에서 정상 종료하며 세션 저장소를 보존합니다. GUI 대신 로컬 프로세스 파이프를 연결하는 Agent용 브라우저입니다.

```json
{"id":"request-1","command":"snapshot","params":{"mode":"viewport","limit":100}}
```

성공은 `{"id":...,"ok":true,"result":...}`, 실패는 `{"id":...,"ok":false,"error":{"code":...,"message":...,"details":...}}`입니다. 잘못된 JSON이나 명령 이후에도 연결은 유지됩니다. 작업 실패는 롤백을 뜻하지 않습니다. `snapshot`/`events`로 실제 결과를 확인한 뒤 재시도하세요. 알 수 없는 매개변수는 `INVALID_ARGUMENT`이며 `details.allowed`에 허용 목록을 돌려줍니다.

## 0.1.0에서 달라진 기본 동작

| 항목 | 0.0.1 | 0.1.0 |
| --- | --- | --- |
| `navigate` 기본 `mode` | `changes`(첫 접속은 사실상 전체 페이지) | `summary`(제목/URL/상태/영역/오류/요소 수/다음 조회 위치) |
| `snapshot` 기본 출력 | 요소 목록 + 텍스트 화면 | `format=text`(텍스트 화면만). `elements`/`both`/`summary` 선택 |
| 요소 표현 | 모든 필드 | `compact=true`: 빈 필드·기본 상태·중복 텍스트·디버깅 필드 생략. `fields`로 선택 |
| 요소 ID | `t1-f1-72cf…-e2470` | `t1e12`(탭 접두사 + 순번). 긴 ID는 `detail.internal_id` |
| 응답 크기 | 제한 없음 | 세션 기본 `--max-bytes 500000`, 요청별 `max_bytes`/`max_elements`/`max_tokens`, `budget` 블록 |
| 조작 응답 | 변경분 | 변경분 + `verification`(전달/수신/기대 상태) + `delta` |
| 대기 조건 | element/text/url/download | + `selector`, `url_changed`, `title`, `value`, `network_idle_ms`, `dom_stable_ms` |

`tab.new`/`tab.switch`도 요약을 돌려줍니다. 0.0.1과 같은 전체 출력이 필요하면 `mode=full`, `format=both`, `compact=false`, `max_bytes=0`을 지정하세요.

## 명령

`tab`을 받는 명령에서 생략하면 현재 활성 탭을 사용합니다. `profile`은 프로필 이름입니다. ID는 응답에서 받은 값을 그대로 사용하며 CSS 선택자/좌표로 해석하지 않습니다(`query`/`extract`의 `scope`/`container`만 예외적으로 ID 또는 CSS 선택자를 받습니다).

공통 **출력 옵션**(아래 표의 `VIEW`): `format=text|elements|both|summary`, `fields=[...]`, `compact=true`, `max_bytes`, `max_elements`, `max_tokens`.

| 명령 | params | 동작 |
| --- | --- | --- |
| `capabilities` | 없음 | 버전, 명령/조작 목록, 요소 필드, 상태 기본값, 대기 조건, tokenizer, 진단 코드, OCR 가능 여부 |
| `profile.create` | `name=default`, `viewport={width,height}`, `mobile=false`, `device_scale_factor=1`, `media_policy=defer` | 영구 프로필 열기; 잔여 잠금은 복구, 사용 중 잠금은 `PROFILE_LOCKED` |
| `profile.list` | 없음 | 열린 프로필과 자원 정책 |
| `profile.close` | `name` | 프로필 종료; 프로세스 종료/잔여 수와 잠금 해제 여부 반환 |
| `tab.new` | `profile?`, `url?` | 새 탭을 만들고 활성화; URL이 있으면 요약 반환 |
| `tab.list` | 없음 | ID, 프로필, URL, 제목, 활성/로딩/충돌 상태 |
| `tab.switch` | `tab` | 활성 탭 전환 및 요약 |
| `tab.close` | `tab?` | 탭 종료 및 남은 탭 목록 |
| `navigate` | `url`, `tab?`, `mode=summary`, `settle_ms=150`, `expect?`, `limit`, `offset`, `include_hidden`, `region`, `max_regions=20`, `max_headings=12`, VIEW | 탐색 후 요약/변경분/화면과 `verification` |
| `snapshot` | `tab?`, `mode=full`, `region?`, `offset=0`, `limit=500`, `include_hidden=false`, `max_regions`, `max_headings`, VIEW | 요약/전체/부분/변경분 관측 |
| `find` | `tab?`, `text?`, `role?`, `region?`, `actionable?`, `include_hidden=true`, `offset=0`, `limit=100`, `match=contains`, `operator=and`, `in=[name,text,value]`, `sort_by=document`, `dedupe=false`, `with_parent=0`, `with_children=0`, `child_roles?`, VIEW | 검색; 아래 "검색" 참고 |
| `detail` | `element`, `tab?`, `radius=2`, `fields?`, `compact=false` | 상세(`internal_id` 포함), 조상, 직계 자식, 앞뒤 문맥 |
| `table` | `element`, `tab?`, `rows?`, `columns?` | 표의 선택한 행/열; 인덱스는 0부터 시작하며 헤더 행 포함 |
| `query` | `tab?`, `frame?`, `selector`, `scope?`, `fields=[id,tag,text,href]`, `attributes=[]`, `text?`, `match=contains`, `include_hidden=false`, `offset=0`, `limit=100`, `dedupe=false`, `max_text=500`, `max_bytes`, `max_elements`, `max_tokens` | 브라우저 내부 CSS 선택자 질의 |
| `extract` | `tab?`, `frame?`, `container?`, `item?`, `fields?`, `text?`, `match`, `include_hidden=false`, `offset=0`, `limit=50`, `dedupe=false`, `max_text=500`, `max_bytes`, `max_elements`, `max_tokens` | 반복 레코드 구조화 추출 |
| `page.state` | `tab?`, `compare?` | 정렬/필터/페이지/전체 건수/페이지당 수/검색어/토글과 `fingerprint` |
| `action` | `action`, `tab?`, `expect?`, `verify=true`, `mode=changes`, `settle_ms=150`, 조작 매개변수, `limit`, `offset`, `include_hidden`, `region`, VIEW | 입력 후 변경분 + `verification` + `delta` |
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
| `wait` | `tab?`, 대기 조건, `timeout_ms?`, VIEW | 모든 조건 충족 대기; 제한 시간 1~30,000ms |
| `doctor` | `launch=true` | 실행 환경 진단(버전/Chromium/사용자/디스플레이/AppArmor/userns/샌드박스/프로세스/잠금) |
| `session.cleanup` | 없음 | 현재 세션 소유가 아닌 잔여 Chromium 프로세스 종료와 잔여 잠금 제거 |
| `close` | 없음 | 모든 프로필을 저장하고 연결 종료; 프로세스/잠금 정리 보고 |

### 화면 모드와 형식

`snapshot.mode`: `summary`, `full`, `viewport`, `main`, `interactive`, `region`, `changes`. `region` 모드는 `region` ID 필수입니다. 페이지네이션 범위는 offset ≥ 0, limit 1~10,000입니다. `omitted.total/filtered/remaining/next_offset/available`로 생략을 알립니다. `changes`는 마지막 **snapshot 또는 조작 응답** 이후 전체 문서 변화만 반환합니다. `find`/`detail`/`table`/`query`/`extract`/`page.state`는 변경분 기준선을 소비하지 않습니다.

`format`:

- `text`(기본): `text` 필드에 들여쓰기된 화면. 한 줄은 `[ID role flags] "이름" = 값 -> href` 형태이며 ID는 JSON과 같습니다.
- `elements`: `elements` 배열만. `both`: 둘 다. `summary`: `mode=summary`와 같습니다.

`summary` 응답: `page`(제목/URL/로딩/HTTP 상태/인증/뷰포트/`stability`), `counts`(전체/표시/숨김/상호작용/링크/버튼/입력/폼/이미지/제목/표/목록/프레임/역할별), `regions`(랜드마크 ID/역할/이름/하위 요소 수), `headings`, `search_box`, `errors`(HTTP 상태/인증/알림/유효성 오류/오류 이벤트/경고), `omitted.elements`와 `omitted.next`(권장 후속 명령).

`page.stability`: `network_inflight`(진행 중 요청 수), `network_quiet_ms`(마지막 네트워크 활동 이후 경과), `dom_quiet_ms`(마지막 DOM 변이 이후 경과; 추적기 미설치 문서는 null).

### 요소 표현과 compact 규칙

전체 필드: `id`, `parent`, `frame`, `role`, `tag`, `name`, `text`, `text_truncated`, `value`, `labels`, `description`, `validation_message`, `href`, `input_type`, `state`, `actions`, `groups`, `bounds`, `style`, `media`, `accessibility_source`, `row_index`, `column_index`, `set_size`, `position_in_set`. `internal_id`는 `detail`에서만 제공합니다.

`compact=true`(기본)에서는 다음을 적용합니다.

- 빈 문자열/`null`/빈 배열, `name`과 같은 `text`는 생략합니다. 그래서 `name`만 있는 요소는 이름이 곧 텍스트입니다.
- `state`는 기본값과 다른 키만 남깁니다. 기본값은 `visible=true`, `in_viewport=true`, `occluded=false`, `disabled=false`, `readonly=false`, `selected=false`, `checked=false`, `expanded=null`, `invalid=false`, `required=false`, `focused=false`, `accessibility_hidden=false`, `actionable=true`이며 모두 기본값이면 `state` 자체를 생략합니다. `capabilities.state_defaults`에서도 확인할 수 있습니다.
- `frame`은 iframe 안의 요소에만 남깁니다. `bounds`, `groups`, `labels`, `style`, `accessibility_source`, `tag`는 `fields`로 요청할 때만 포함합니다.
- `fields=[...]`가 있으면 `id`와 요청한 필드만 그대로(compact 규칙 없이) 돌려줍니다.

`state` 전체 의미: `visible`, `in_viewport`, `occluded`, `disabled`, `readonly`, `selected`, `checked`(true/false/`mixed`), `expanded`(true/false/null), `invalid`, `required`, `focused`, `accessibility_hidden`, `actionable`. `bounds`는 현재 뷰포트 기준 CSS 픽셀이며 iframe 위치를 합산합니다. `media`: alt, source, size, loaded, needs_visual, context. `name`은 최대 4,000자, `text`는 최대 8,000자이며 잘림은 `text_truncated`로 명시합니다.

### 요소 ID

Agent용 ID는 `t1e12`처럼 **탭 ID + e + 순번**입니다. 같은 탭의 모든 프레임이 순번을 공유하므로 탭·프레임 사이에서 충돌하지 않으며, 같은 DOM 노드는 세션 동안 같은 ID를 유지합니다. 문서가 교체되면 이전 ID는 `STALE_ELEMENT`가 됩니다. `detail.element.internal_id`의 긴 ID(`t1-f1-…-e2470`)는 디버깅용이며, 이전 버전과의 호환을 위해 어떤 명령에서든 요소 ID 자리에 사용할 수 있습니다.

### 응답 예산(`budget`)

모든 관측 명령(`navigate`, `snapshot`, `find`, `query`, `extract`, `wait`, `action`)은 `budget`을 포함합니다.

```json
{"bytes":18342,"elements":120,"estimated_tokens":6120,"tokenizer":"heuristic-v1 (cjk=1/char, ascii=1/4chars, other=1/2chars)",
 "limits":{"max_bytes":500000,"max_elements":null,"max_tokens":null},"truncated":true,"truncated_by":"max_elements","next_offset":120}
```

- `bytes`는 result 전체의 실제 UTF-8 바이트, `estimated_tokens`는 같은 내용의 추정 토큰입니다. 추정은 외부 tokenizer 없이 문자 종류별 가중치로 계산한 근사값이므로 상한에 여유를 두세요.
- `max_bytes`/`max_tokens`는 요소 목록(또는 텍스트 줄)을 담다가 한도를 넘기 직전에 멈춥니다. 첫 요소만으로 한도를 넘으면 하나는 포함하고 `oversized=true`로 표시합니다. 페이지/이벤트 같은 고정 오버헤드는 한도 계산에 포함하지 않습니다.
- `max_elements`는 요소 수 상한입니다. `0`이나 `null`은 해당 한도를 끕니다. 세션 기본 바이트 상한은 `--max-bytes`(기본 500,000, 0이면 무제한)입니다.
- 잘렸으면 `truncated=true`, `truncated_by`, `next_offset`을 돌려주며, 같은 명령에 `offset=next_offset`을 지정해 이어서 조회합니다.

### 검색(`find`)

- `text`: 문자열 또는 문자열 배열. `match`: `contains`(기본, 대소문자 무시) / `exact`(필드 전체 일치) / `prefix` / `regex`(대소문자 무시, 500자 이하). 배열은 `operator=and|or`로 결합합니다.
- `in`: 검색 대상 필드(`name`, `text`, `value`, `href`, `description`). 기본은 `name`, `text`, `value`입니다.
- `role`: 문자열 또는 배열. `region`: 영역 ID. `actionable=true`: 조작 가능 요소만.
- `sort_by`: `document`(문서 순, 기본) / `name` / `role` / `position`(화면 y, x).
- `dedupe`: `true`(role+name+href 기준) 또는 필드 이름 배열. `duplicates_removed`로 제거 수를 알립니다.
- `with_parent=n`: 가장 가까운 조상 n개를 `ancestors`(`{id, role, name, href?, value?}`)로 반환합니다.
- `with_children=n`: 깊이 n까지 자식을 `children`으로 반환합니다. `child_roles=[...]`가 있으면 해당 역할의 후손만 `depth`와 함께 평면 목록으로 돌려주고 관련 없는 후손은 제외합니다. 자식은 요소당 50개까지이며 초과분은 `children_omitted`입니다.
- 응답: `elements`, `total`(중복 제거 후 일치 수), `omitted`, `next_offset`, `duplicates_removed`, `warnings`, `budget`.

### 브라우저 내부 질의(`query`)

CSS 선택자로 요소를 Chromium 안에서 고르고 요청한 필드만 돌려줍니다. 전체 DOM은 전송하지 않습니다.

- `selector`: CSS 선택자(1~1000자). 문법 오류는 `INVALID_ARGUMENT`입니다.
- `scope`: 요소 ID 또는 CSS 선택자. 그 요소의 **후손**에서만 찾습니다. `frame`: 프레임 ID(기본 주 프레임).
- `fields`: `id`, `tag`, `text`, `name`(aria-label/alt/title/placeholder), `role`, `href`(절대 URL), `src`, `value`, `visible`, `classes`, `bounds`, `price`(`{raw, value, currency}`), `number`, `attributes`. `attributes=[...]`로 지정한 속성은 `@속성명` 키로 돌려줍니다(`fields`에 `attributes`가 있으면 `attributes` 객체로).
- `text`+`match`: 요소 텍스트/이름 조건. `include_hidden=false`: 숨긴 요소 제외. `dedupe`: `true`(text+href) 또는 필드 배열. `max_text`: 텍스트 길이 상한.
- 응답: `items`, `total`(선택자 일치 수), `matched`(조건/중복 제거 후), `offset`, `next_offset`, `omitted`, `duplicates_removed`, `frame`, `budget`. `id`가 있는 항목은 바로 `action`/`detail`에 쓸 수 있습니다.

임의 JavaScript는 실행하지 않습니다. 선택자·필드·속성 이름은 데이터로만 전달되며 쿠키·저장소·네트워크 API에는 접근하지 않습니다.

### 반복 레코드 추출(`extract`)

상품 카드, 게시글, 검색 결과처럼 같은 구조가 반복되는 항목을 레코드 단위로 묶습니다. 한 레코드의 필드는 그 레코드 요소 안에서만 읽으므로 다른 레코드의 값과 섞이지 않습니다.

- `container`: 요소 ID 또는 CSS 선택자(기본 문서 본문). `item`: 레코드 요소 선택자. 생략하면 같은 서명(태그+클래스)을 가진 형제가 3개 이상이고 내용이 있는 그룹을 자동 감지하며, `detection`에 방법/서명/컨테이너 ID/후보 목록을 돌려줍니다.
- `fields` 생략 시 자동 필드: `title`, `url`, `price`, `shipping`(있을 때), `image`, `text`. 지정 시 `{이름: "선택자"}` 또는 `{이름: {selector?, attr?, type=text|number|price|url, all=false, regex?}}`. `selector`를 생략하면 레코드 요소 자체를 읽습니다. `regex`의 첫 캡처 그룹(없으면 전체 일치)을 값으로 씁니다.
- 레코드: `{index, id, fields, missing?}`. `missing`은 필드별 누락 원인(`no_match`, `attribute_empty`, `empty_text`, `regex_no_match`)입니다. 누락 필드 값은 `null`입니다.
- `dedupe`: `true`(url, 없으면 title+price) 또는 필드 배열. `text`/`match`로 레코드 텍스트 조건을 걸 수 있습니다.
- 응답: `records`, `total`, `matched`, `duplicates_removed`, `detection`, `offset`, `next_offset`, `omitted`, `missing_fields`, `frame`, `budget`.

### 페이지 상태(`page.state`)

검색 결과 페이지의 조건을 구조화합니다. 사이트마다 마크업이 달라 휴리스틱이며 각 항목에 `source`와 후보를 함께 돌려줍니다.

- `url_params`, `query`(검색어: URL 또는 검색 입력).
- `sort`: `{value, label, source=url|select|active_control, id?, options?}`와 `sort_candidates`.
- `pagination`: `current_page`, `total_pages`, `has_next`, `has_prev`, `next`/`prev`(요소 ID), `pages[{page,id,active}]`, `source`, `element`.
- `per_page`: `{value, source, options?}`, `total_results`: `{value, text, source}`.
- `filters`: 체크된 체크박스/라디오, aria-pressed/aria-selected 칩, 정렬/페이지/검색어가 아닌 URL 매개변수. `toggles`: 배송비 포함·무료배송 같은 결과에 영향을 주는 체크 요소와 현재 상태.
- `fingerprint`: `{sort, filters, per_page, query}`. `compare`에 이전 응답의 `fingerprint`를 넘기면 `preserved`와 `differences`로 페이지 이동 후 조건 유지 여부를 검증합니다.
- `checks`: `sort_detected`, `pagination_detected`, `total_results_detected`, `has_next`.

### 대기 조건(`wait`, `action.expect`, `navigate.expect`)

| 조건 | 의미 |
| --- | --- |
| `element` + `state` | 요소 ID의 `visible`/`hidden`/`attached`/`detached`/`enabled` |
| `text` + `state` | 이름/텍스트/값에 부분 문자열을 가진 요소의 상태 |
| `selector` + `state` | 주 프레임 CSS 선택자 일치 요소의 상태 |
| `value` | `element` 또는 `selector` 요소의 현재 값이 같음(선택 상태 변경 대기) |
| `url` | 현재 URL에 부분 문자열 포함 |
| `url_changed=true` | 대기/조작 시작 시점과 URL이 다름 |
| `title` | 제목에 부분 문자열 포함 |
| `network_idle_ms` | 진행 중 요청이 없고 마지막 네트워크 활동 뒤 n ms 경과 |
| `dom_stable_ms` | 마지막 DOM 변이 뒤 n ms 경과(화면 안정화) |
| `download` | 다운로드 ID 또는 `any` 완료 |

모든 조건이 동시에 충족돼야 합니다. `wait`는 변경분과 `wait.met`/`waited_ms`를 돌려주고, 시간 초과는 `WAIT_TIMEOUT` 오류의 `details.unmet`/`met`에 조건별 기대값과 실제값을 담습니다. 존재하지 않는 텍스트를 `hidden`/`detached`로 기다리면 즉시 성공합니다.

## 조작

공통 `mode=changes`, `settle_ms=150`(0~5,000ms), `tab?`, `expect?`, `verify=true`를 지원합니다.

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

### 조작 검증(`verification`)

```json
{"action_dispatched":true,"event_received":true,"events":["pointerdown","mousedown","click"],"navigation":false,
 "expected_state_reached":true,"met":[{"condition":"url","expected":"sort=low","actual":"https://…?sort=low"}],"unmet":[],"waited_ms":412}
```

- `action_dispatched`: Playwright가 입력을 오류 없이 전달했습니다.
- `event_received`: 조작 직전 요소(또는 문서)에 설치한 캡처 리스너가 해당 DOM 이벤트(`events`)를 실제로 받았습니다. `false`면 가림/비활성화/리스너 없음을 의심하세요. 문서가 교체되어 읽을 수 없으면 `null`이며 `reason`을 함께 줍니다. `back`/`forward`/`reload`는 탐색 이벤트 관측 여부입니다. `verify=false`면 탐지기를 설치하지 않습니다.
- `navigation`: 조작 이후 URL 변경 또는 탐색 이벤트가 있었는지.
- `expected_state_reached`: `expect`를 지정했을 때 기대 상태 도달 여부. 도달하지 못하면 응답은 `ok=false`, `error.code=EXPECTATION_NOT_MET`이며 `details.verification`/`unmet`/`met`/`reason`/`page`를 돌려줍니다. 조작 자체는 이미 실행된 상태입니다.
- `delta.url`: 조작 전후 URL(`{before, after}`, 변화가 없으면 null). 변경분 모드의 `changes.added/removed/changed`로 요소 수준 차이를 비교합니다.

사용자 정의 드롭다운은 `click` 후 항목 ID를 다시 조회해 선택합니다. Escape를 처리하지 않는 사용자 정의 모달은 닫기 버튼을 클릭하세요. 임의 JavaScript 평가/강제 클릭 명령은 제공하지 않습니다. 키보드는 해당 OS의 실제 단축키 의미를 따릅니다(macOS 줄 끝 이동 `Meta+ArrowRight`, Windows/Linux `End`, 전체 선택 `ControlOrMeta+A`).

## 렌더

```json
{"id":10,"command":"render","params":{"element":"t1e42","level":"original","ocr":true,"ocr_language":"kor+eng"}}
{"id":11,"command":"render","params":{"target":"region","clip":{"x":0,"y":0,"width":640,"height":480},"level":"preview"}}
```

- `target`: `element`(element 필수), `viewport`(기본), `page`(스크롤 전체), `region`(clip 필수).
- `level`: `preview`(긴 변 최대 480px), `normal`(CSS 해상도), `original`(이미지는 지원되는 원본 파일 픽셀, 그 외 장치 해상도).
- `load_media=true`: 요청 범위의 이미지 URL을 허용하고 재로딩. `false`이면 현재 로딩 상태 그대로 촬영.
- `base64=false`: 기본 PNG 파일 경로만 반환. true일 때 PNG 바이트의 base64도 반환.
- `ocr=false`, `ocr_language=eng`: 로컬 Tesseract 추출. 미설치 시 `ocr.status=unavailable`, 언어팩 오류 시 failed.
- 응답: path/mime_type/bytes/size/target/level/rendered/description 및 선택적 base64/ocr. description은 페이지 메타데이터이며 AI가 추론한 이미지 설명이 아닙니다.

## 진단과 정리

`doctor`(또는 CLI `--doctor`)는 `aribrowser`, `platform`, `user{name,uid,is_root}`, `display{DISPLAY,WAYLAND_DISPLAY,XAUTHORITY,xauthority_exists}`, `chromium{chromium,headless_shell,launch_path,browsers_root,version}`, `apparmor`, `userns`, `suid_sandbox`, `launch_env`, `sandbox{ok,sandbox_active,code,message,fix,seccomp}`, `profiles`(잠금 상태), `processes`, `checks[{id,status=ok|warn|fail,code,message}]`, `summary{ok,error_code,fix,warnings}`를 돌려줍니다. 프로토콜 `doctor`는 `session{open_profiles,tabs,own_processes,foreign_processes}`를 추가합니다. 진단 코드와 조치는 [SANDBOX.md](SANDBOX.md)에 있습니다.

`close` 응답:

```json
{"closed":true,"profiles":[{"closed":"default","processes":{"before":6,"exited":6,"forced":0,"remaining":[]},"lock":{"released":true,"path":null,"removed":[],"remaining":false}}],
 "processes":{"terminated":6,"forced":0,"remaining":0,"remaining_pids":[]},"locks":{"released":true,"remaining":[]}}
```

## 이벤트와 오류

이벤트는 seq/time/type과 tab/profile 등의 문맥을 갖습니다. `loading_started`, `dom_ready`, `loading_finished`, `navigation`, `frame_navigation`, `redirect`, `tab_created`, `tab_closed`, `popup_created`, `dialog_opened`, `dialog_closed`, `download_started`, `download_completed`, `download_failed`, `network_failed`, `not_found`, `authentication_required`, `server_error`, `http_error`, `script_error`, `browser_crashed`, `profile_closed`, `profile_lock_recovered`, `background_error`를 제공합니다. 버퍼 초과는 `events_omitted=true`로 표시합니다.

주요 오류 코드는 `INVALID_JSON`, `INVALID_REQUEST`, `INVALID_ARGUMENT`, `UNKNOWN_COMMAND`, `TAB_NOT_FOUND`, `PROFILE_NOT_FOUND`, `PROFILE_EXISTS`, `PROFILE_LOCKED`, `ELEMENT_NOT_FOUND`, `STALE_ELEMENT`, `ELEMENT_NOT_ACTIONABLE`, `ACTION_FAILED`, `EXPECTATION_NOT_MET`, `NETWORK_FAILED`, `BROWSER_UNAVAILABLE`, `BROWSER_CRASHED`, `BROWSER_ERROR`, `BROWSER_LAUNCH_FAILED`, `PAGE_UNRESPONSIVE`, `WAIT_TIMEOUT`, `FILE_NOT_FOUND`, `RESOURCE_NOT_FOUND`, `RESOURCE_FAILED`, `IO_ERROR`, `SESSION_CLOSED`와 실행 환경 코드 `CHROMIUM_MISSING`, `CHROMIUM_DEPENDENCIES_MISSING`, `SANDBOX_ROOT_USER`, `SANDBOX_USERNS_RESTRICTED`, `SANDBOX_PROFILE_STALE`, `SANDBOX_PROFILE_NOT_LOADED`, `SANDBOX_LAUNCH_FAILED`, `DISPLAY_MISSING`입니다. HTTP 404/500은 렌더 가능한 문서이므로 탐색은 성공 응답이며 `page.http_status`, 요약의 `errors.http_status`와 이벤트에 오류를 표시합니다.
