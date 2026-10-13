# AriBrowser JSONL v1 (0.3.0)

UTF-8 JSON 객체 하나를 한 줄에 전송합니다. 요청은 순차 실행되며 `id`가 응답에 그대로 전달됩니다. 브라우저는 첫 프로필/탐색 요청에서 시작됩니다. EOF 또는 `close`에서 정상 종료하며 세션 저장소를 보존합니다. GUI 대신 로컬 프로세스 파이프를 연결하는 Agent용 브라우저입니다.

```json
{"id":"request-1","command":"snapshot","params":{"mode":"viewport","limit":100}}
```

성공은 `{"id":...,"ok":true,"result":...}`, 실패는 `{"id":...,"ok":false,"error":{"code":...,"message":...,"details":...}}`입니다. 잘못된 JSON이나 명령 이후에도 연결은 유지됩니다. 작업 실패는 롤백을 뜻하지 않습니다. `snapshot`/`events`로 실제 결과를 확인한 뒤 재시도하세요. 알 수 없는 매개변수는 `INVALID_ARGUMENT`이며 `details.allowed`에 허용 목록을 돌려줍니다.

## 0.3.0에서 달라진 기본 동작

| 항목 | 0.2.0 | 0.3.0 |
| --- | --- | --- |
| `dedupe` 키가 모두 null인 레코드 | `[null]` 키로 서로 중복 처리(레코드 손실) | 비교에서 제외하고 보존. `dedupe{usable_records, skipped_records, key_coverage, warning}` 추가 |
| `quality` | `confidence`, `coverage`, `records`, `hint` | + `source_records`, `output_records`, `deduplicated`, `duplicate_ratio`, `dedupe_key_coverage`, `missing`, `warnings`. 제거율/커버리지 이상이면 `high`를 주지 않음 |
| `missing` 사유 | `no_match`, `attribute_empty`, `empty_text`, `regex_no_match` | `selector_no_match`, `relation_no_match`, `attribute_empty`, `text_empty`, `regex_no_match`, `parse_failed`, `unsupported_locale_pattern` |
| 필드 명세 `selector: ":scope"` | null(item.querySelectorAll에 걸리지 않음) | 레코드 자체(`source: "self"`, `":self"`와 같음) |
| `type: "url"` 필드의 href 없는 요소 | null | 가장 가까운/내부 `a[href]`로 자동 탐색, 출처는 `record.sources` |
| 자동 필드 | …`sold`, `rating`, `reviews` | + `recent_sales`(기간 한정 구매량). `sold`는 기간 표현을 제외 |
| 평점 해석 | 값이 먼저 나오는 표현만 | + `별 5개 중 4.3개`, `5점 만점에 4.3점`, `5개 중 4.3` |
| `profile.create` | viewport/mobile/device_scale_factor/media_policy | + `locale`, `timezone_id`; 응답에 `settings`/`runtime`; 설정은 프로필에 저장되어 재실행 시 유지 |
| `navigate` 응답 | 요약 + readiness + include | + `runtime`(첫 응답 전체, 이후 `runtime_id`). `mode="minimal"`로 요약 생략 |
| `page.product` | 상품 요약 | + `recent_sales`, `canonical_url`, `stable_id`, `canonical_source` |

## 0.2.0에서 달라진 기본 동작

| 항목 | 0.1.0 | 0.2.0 |
| --- | --- | --- |
| 관측 응답의 이벤트 | `events` 원문 배열(광고/추적 URL 포함) | `activity` 집계(종류별 수, 오류 몇 건의 축약 URL). 원문은 `include_events=true` 또는 `events(since)` |
| `navigate` 성공 조건 | `load` 완료(빈 껍데기도 성공) | 로딩 품질 검증: `readiness.status`가 `empty`/`blocked`면 `PAGE_EMPTY`/`PAGE_BLOCKED` 오류. `ready=false`로 생략, `ready.on_fail=warn`으로 경고화 |
| `navigate`의 `load` 대기 | 최대 10초 | 검증이 켜져 있으면 2초(내용 판정이 대신함) |
| `navigate` 한 번에 얻는 것 | 요약 | 요약 + `include`로 레코드/상품/상태/이벤트, 새 `batch` 명령 |
| 요약 관측 | Chromium AX 보강 포함 | AX 보강 생략(대형 페이지 수 초 단축). 상세 모드는 그대로 |
| `extract` 자동 필드 | title/url/price/shipping/image/text | + `original_price`/`discount`/`sold`/`rating`/`reviews`, `quality` 블록, 경계 규칙으로 조립한 `text` |
| 상품 상세 | `query`/`snapshot`으로 직접 읽음 | `page.product`가 JSON-LD/메타/DOM을 합쳐 요약 |
| 실행 파일 | 번들 Chromium | `--browser auto`: 번들이 샌드박스 문제로 실패하면 시스템 Chrome/Chromium/Edge/Brave |
| 샌드박스 설치 | 설치 경로별 AppArmor 정책 | 호스트당 한 번 설치하는 공용 setuid 도우미(기본) 또는 경로 독립 AppArmor 정책 |

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

공통 **출력 옵션**(아래 표의 `VIEW`): `format=text|elements|both|summary`, `fields=[...]`, `compact=true`, `max_bytes`, `max_elements`, `max_tokens`, `include_events=false`.

| 명령 | params | 동작 |
| --- | --- | --- |
| `capabilities` | 없음 | 버전, 명령/조작 목록, 요소 필드, 상태 기본값, 대기 조건, tokenizer, 진단 코드, OCR 가능 여부, 브라우저 정책/실제 실행 파일, `runtime`, readiness 상태, include 종류/탐색 모드, 자동 필드/필드 명세 키/누락 사유, 프로필 설정 |
| `profile.create` | `name=default`, `viewport={width,height}`, `mobile=false`, `device_scale_factor=1`, `media_policy=defer`, `locale?`, `timezone_id?` | 영구 프로필 열기(아래 "프로필 설정"); 잔여 잠금은 복구, 사용 중 잠금은 `PROFILE_LOCKED`. 응답에 `settings`와 `runtime` |
| `profile.list` | 없음 | 열린 프로필과 자원 정책·적용 설정(locale/timezone_id/viewport/mobile/device_scale_factor), `runtime` |
| `profile.close` | `name` | 프로필 종료; 프로세스 종료/잔여 수와 잠금 해제 여부 반환 |
| `tab.new` | `profile?`, `url?` | 새 탭을 만들고 활성화; URL이 있으면 요약 반환 |
| `tab.list` | 없음 | ID, 프로필, URL, 제목, 활성/로딩/충돌 상태 |
| `tab.switch` | `tab` | 활성 탭 전환 및 요약 |
| `tab.close` | `tab?` | 탭 종료 및 남은 탭 목록 |
| `navigate` | `url`, `tab?`, `mode=summary`(또는 `minimal`/`full`/`viewport`/`main`/`changes`), `settle_ms=150`, `expect?`, `ready=true`, `include=[]`, `extract={}`, `limit`, `offset`, `include_hidden`, `region`, `max_regions=20`, `max_headings=12`, VIEW | 탐색 → 로딩 품질 검증(재시도) → 요약/최소/변경분/화면 + `verification` + `readiness` + include 결과 + `runtime` |
| `snapshot` | `tab?`, `mode=full`, `region?`, `offset=0`, `limit=500`, `include_hidden=false`, `max_regions`, `max_headings`, VIEW | 요약/전체/부분/변경분 관측 |
| `find` | `tab?`, `text?`, `role?`, `region?`, `actionable?`, `include_hidden=true`, `offset=0`, `limit=100`, `match=contains`, `operator=and`, `in=[name,text,value]`, `sort_by=document`, `dedupe=false`, `with_parent=0`, `with_children=0`, `child_roles?`, VIEW | 검색; 아래 "검색" 참고 |
| `detail` | `element`, `tab?`, `radius=2`, `fields?`, `compact=false` | 상세(`internal_id` 포함), 조상, 직계 자식, 앞뒤 문맥 |
| `table` | `element`, `tab?`, `rows?`, `columns?` | 표의 선택한 행/열; 인덱스는 0부터 시작하며 헤더 행 포함 |
| `query` | `tab?`, `frame?`, `selector`, `scope?`, `fields=[id,tag,text,href]`, `attributes=[]`, `text?`, `match=contains`, `include_hidden=false`, `offset=0`, `limit=100`, `dedupe=false`, `max_text=500`, `max_bytes`, `max_elements`, `max_tokens` | 브라우저 내부 CSS 선택자 질의(필드에 `sold`/`rating`/`reviews`/`discount` 포함) |
| `extract` | `tab?`, `frame?`, `container?`, `item?`, `fields?`, `text?`, `match`, `include_hidden=false`, `offset=0`, `limit=50`, `dedupe=false`, `explain=false`, `max_text=500`, `max_bytes`, `max_elements`, `max_tokens` | 반복 레코드 구조화 추출 + `quality` + `dedupe` 통계 |
| `page.state` | `tab?`, `compare?` | 정렬/필터/페이지/전체 건수/페이지당 수/검색어/토글과 `fingerprint` |
| `page.product` | `tab?` | 상품 상세 요약(제목/가격/원가/할인/판매량/최근 구매량/평점/리뷰/배송/재고/브랜드/판매자/옵션/이미지)과 출처, canonical URL/안정 식별자 |
| `batch` | `steps=[{command, params, id?}]`(1~20), `stop_on_error=true` | 여러 명령을 한 왕복에 순차 실행; `batch`/`close`는 포함 불가 |
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

### 프로필 설정(`profile.create.locale`, `timezone_id`)

같은 URL이라도 브라우저 로캘에 따라 상품명·평점 문자열·통화·날짜가 달라져 Agent 간 결과가 재현되지 않습니다. `locale`(BCP 47, 예: `en-US`, `ko-KR`; `en_US`는 `en-US`로 정규화)은 `Accept-Language` 헤더와 `navigator.language`에, `timezone_id`(IANA, 예: `UTC`, `Asia/Seoul`)는 JavaScript `Date`/`Intl` 시간대에 적용됩니다. 잘못된 값은 `INVALID_ARGUMENT`입니다.

```json
{"id":1,"command":"profile.create","params":{"name":"benchmark","locale":"en-US","timezone_id":"UTC","viewport":{"width":1280,"height":800}}}
```

응답: `{"profile":"benchmark","persistent":true,"media_policy":"defer","settings":{"locale":"en-US","timezone_id":"UTC","viewport":{…},"mobile":false,"device_scale_factor":1,"media_policy":"defer"},"locale":"en-US","timezone_id":"UTC","tabs":[…],"runtime":{…}}`. 적용 설정은 프로필 디렉터리의 `aribrowser-profile.json`에 저장되어 같은 이름의 프로필을 옵션 없이 다시 열면 그대로 쓰입니다(명시한 값 > 저장된 값 > CLI `--locale`/`--timezone` 세션 기본값). `profile.list`와 `capabilities.profiles`에서 확인할 수 있습니다.

### 최소 탐색(`navigate.mode="minimal"`)

사이트 구조를 이미 알아 `include`로 구조화 결과만 필요할 때 씁니다. 관측(DOM 수집)과 요약을 생략하므로 영역/제목/역할 수/검색창/활동 집계가 없고 시간도 줄어듭니다. readiness 판정과 오류 응답(`PAGE_EMPTY`/`PAGE_BLOCKED`)은 그대로입니다.

```json
{"id":1,"command":"navigate","params":{"url":"https://www.amazon.com/s?k=guitar+headphone+amp","mode":"minimal","include":["records"],
 "extract":{"item":"[data-asin]:not([data-asin=''])","limit":60,"fields":{"title":"h2","url":{"selector":"h2","type":"url","canonical":true},
            "price":{"selector":".a-price .a-offscreen","type":"price"},"rating":{"selector":".a-icon-alt","type":"rating"},"recent":{"source":"self"}}}}}
```

응답: `page{url,title,http_status}`, `readiness{status,reasons,kind,waited_ms,attempts,hint?,signals?(ready가 아닐 때),probe?}`, `verification`, 요청한 `records`/`product`/`state`/`events`, `errors`(HTTP 4xx/5xx 또는 오류성 이벤트가 있을 때만: `http_status`, `event_counts`, `events` 3건), `runtime`, `cursor`, `budget`. minimal 탐색 뒤의 첫 `action`/`snapshot(mode=changes)`은 새 기준선을 잡습니다.

### 실행 엔진 정보(`runtime`)

첫 `profile.create` 또는 첫 `navigate` 응답과 `capabilities.runtime`에 실제 실행 엔진을 돌려줍니다. 이후 응답은 반복 payload를 줄이기 위해 `{"runtime_id":"rt…"}`만 싣습니다.

```json
{"runtime_id":"rt3f9a1c","kind":"chrome","product":"HeadlessChrome/146.0.7680.80","version":"146.0.7680.80",
 "executable":"/opt/google/chrome/chrome","resolved_executable":"/opt/google/chrome/chrome","fallback_from":"SANDBOX_USERNS_RESTRICTED",
 "sandbox_active":true,"sandbox_source":"namespace_or_profile","sandbox_evidence":{"seccomp_filtered":true},"headless":true,"user_agent":"Mozilla/5.0 …"}
```

`version`은 CDP `Browser.getVersion`의 제품 문자열에서, `resolved_executable`은 Linux에서 브라우저 주 프로세스의 `/proc/<pid>/exe`에서 읽습니다(launcher 이름으로 추정하지 않음). `kind`는 `bundled`/`chrome`/`chromium`/`msedge`/`brave`/`custom`, `fallback_from`은 번들 Chromium이 실패했을 때의 코드, `sandbox_source`는 `setuid_helper`/`namespace_or_profile`/`none`(`--no-sandbox`)입니다.

### 화면 모드와 형식

`snapshot.mode`: `summary`, `full`, `viewport`, `main`, `interactive`, `region`, `changes`. `region` 모드는 `region` ID 필수입니다. 페이지네이션 범위는 offset ≥ 0, limit 1~10,000입니다. `omitted.total/filtered/remaining/next_offset/available`로 생략을 알립니다. `changes`는 마지막 **snapshot 또는 조작 응답** 이후 전체 문서 변화만 반환합니다. `find`/`detail`/`table`/`query`/`extract`/`page.state`는 변경분 기준선을 소비하지 않습니다.

`format`:

- `text`(기본): `text` 필드에 들여쓰기된 화면. 한 줄은 `[ID role flags] "이름" = 값 -> href` 형태이며 ID는 JSON과 같습니다.
- `elements`: `elements` 배열만. `both`: 둘 다. `summary`: `mode=summary`와 같습니다.

`summary` 응답: `page`(제목/URL/로딩/HTTP 상태/인증/뷰포트/`stability`), `kind`(페이지 종류 추정: `listing`/`product`/`login`/`article`/`other`), `counts`(전체/표시/숨김/상호작용/링크/버튼/입력/폼/이미지/제목/표/목록/프레임/역할별), `regions`(랜드마크 ID/역할/이름/하위 요소 수), `headings`, `search_box`, `errors`(HTTP 상태/인증/알림/유효성 오류/최근 오류 이벤트 3건(축약 URL)/`event_counts`/경고), `omitted.elements`와 `omitted.next`(페이지 종류별 권장 후속 명령). 요약 관측은 Chromium AX 보강을 생략합니다(이름은 DOM/ARIA 기준).

### 활동 집계(`activity`)

모든 관측 응답은 이벤트 원문 대신 다음 집계를 싣습니다.

```json
{"since":30,"cursor":64,"events":34,"by_type":{"navigation":2,"frame_navigation":12,"network_failed":12,"redirect":3},
 "errors":[{"type":"network_failed","url":"https://analytics.google.com/g/collect","message":"net::ERR_ABORTED"}],
 "errors_total":12,"redirects":3,"frame_navigations":12,"final_url":"https://…","events_omitted":false}
```

`errors`는 오류성 이벤트(`network_failed`, `not_found`, `authentication_required`, `server_error`, `http_error`, `script_error`, `browser_crashed`, `download_failed`, `background_error`) 최대 5건이며 URL은 호스트+경로 100자로 축약합니다. 원문이 필요하면 같은 명령에 `include_events=true`를 주거나 `events` 명령에 `since=activity.since`를 넘기세요.

### 로딩 품질 검증(`navigate.ready`, `readiness`)

`navigate`는 `load` 뒤에 페이지가 실제 내용을 담았는지 판정합니다. 응답의 `verification.content_ready`와 `readiness`:

```json
{"status":"ready","reasons":[],"kind":"listing","waited_ms":142,"checks":1,"attempts":1,"probe":null,
 "signals":{"text_chars":5948,"content_text_chars":5200,"nodes":9800,"links":519,"images":117,"headings":19,"h1":"아크릴","inputs":4,
            "password_inputs":0,"skeletons":0,"spinners":0,"app_root":null,"captcha":[],"repeated_groups":12,"prices":40,"sample":"…"}}
```

| `status` | 의미 | 응답 |
| --- | --- | --- |
| `ready` | 내용 텍스트가 충분하고 차단/로딩 표시가 없음 | 성공, `content_ready=true` |
| `loading` | 제한 시간 안에 `ready.selector`/`text` 조건이 충족되지 않음 | 성공, `content_ready=false` |
| `empty` | 제한 시간과 재시도 뒤에도 내용 텍스트가 `min_text`보다 적음(빈 껍데기, SPA 루트 비어 있음) | `PAGE_EMPTY` 오류 |
| `blocked` | 보안 문자 프레임/스크립트/요소, 차단 문구, HTTP 403/429 | `PAGE_BLOCKED` 오류(재시도 없음) |
| `login_required` | 비밀번호 입력이 보이고 그 밖의 내용이 거의 없음 | 성공, `content_ready=false` |
| `http_error` | HTTP 4xx/5xx 문서(404 등) | 성공, `content_ready=false` |
| `error` | Chromium 오류 페이지 | `NETWORK_FAILED` 오류 |

`ready` 매개변수: `true`(기본), `false`(검사 생략, `readiness` 없음), 또는 `{min_text=120, selector?, text?, timeout_ms=min(--timeout-ms, 8000), retry=1, on_fail=error|warn}`. `selector`/`text`를 주면 그 요소/문구가 보일 때까지 기다립니다. `empty`/`loading`은 `retry`만큼 새로 고쳐 다시 판정하며 `navigation_retry` 이벤트를 남깁니다. 오류 응답의 `details`에는 `readiness`, `page{url,title,http_status}`, `verification`, `hint`(다음 조치), `recovery`가 있고 탭에는 현재 문서가 남아 있어 `snapshot`/`query`로 내용을 볼 수 있습니다. 작은 정적 문서(노드 수십 개, SPA 루트 없음)는 텍스트가 적어도 `ready`입니다. 판정은 휴리스틱이므로 `signals`로 근거를 확인하세요.

### 한 왕복 조회(`navigate.include`, `batch`)

`include`에 `records`(`extract` 매개변수는 `extract={...}`로), `product`(`page.product`), `state`(`page.state`), `events`(원문)를 주면 탐색 응답의 같은 키에 결과를 싣습니다. 각 결과는 자체 `budget`을 가집니다.

```json
{"id":1,"command":"navigate","params":{"url":"https://…/search?q=아크릴","include":["records","state"],"extract":{"limit":20,"dedupe":true}}}
{"id":2,"command":"batch","params":{"steps":[{"command":"navigate","params":{"url":"https://…/item/1"}},{"id":"p","command":"page.product","params":{}}]}}
```

`batch` 응답: `results`(단계별 `{id, ok, result|error}`), `completed`, `failed`, `stopped_at`(실패로 멈춘 단계 번호 또는 null), `total`. 단계는 각각 검증되며 `stop_on_error=false`면 실패 뒤에도 계속합니다.

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
- `fields`: `id`, `tag`, `text`(경계 규칙으로 조립한 표시 텍스트), `name`(aria-label/alt/title/placeholder), `role`, `href`(절대 URL), `src`, `value`, `visible`, `classes`, `bounds`, `price`(`{raw, value, currency}`), `number`, `sold`(`{raw, value, plus}`), `recent_sales`(`{raw, value, plus, period}`), `rating`(`{raw, value, reviews?}`), `reviews`(`{raw, value, plus}`), `discount`(`{raw, percent}`), `attributes`. `attributes=[...]`로 지정한 속성은 `@속성명` 키로 돌려줍니다(`fields`에 `attributes`가 있으면 `attributes` 객체로).
- `text`+`match`: 요소 텍스트/이름 조건. `include_hidden=false`: 숨긴 요소 제외. `dedupe`: `true`(text+href) 또는 필드 배열. `max_text`: 텍스트 길이 상한.
- 응답: `items`, `total`(선택자 일치 수), `matched`(조건/중복 제거 후), `offset`, `next_offset`, `omitted`, `duplicates_removed`, `dedupe`(요청 시: `keys`, `usable_records`, `skipped_records`, `key_coverage`, `warning?`; 키가 모두 비어 있는 항목은 보존), `frame`, `budget`. `id`가 있는 항목은 바로 `action`/`detail`에 쓸 수 있습니다.

임의 JavaScript는 실행하지 않습니다. 선택자·필드·속성 이름은 데이터로만 전달되며 쿠키·저장소·네트워크 API에는 접근하지 않습니다.

### 반복 레코드 추출(`extract`)

상품 카드, 게시글, 검색 결과처럼 같은 구조가 반복되는 항목을 레코드 단위로 묶습니다. 한 레코드의 필드는 그 레코드 요소 안에서만 읽으므로 다른 레코드의 값과 섞이지 않습니다.

- `container`: 요소 ID 또는 CSS 선택자(기본 문서 본문). `item`: 레코드 요소 선택자. 생략하면 같은 서명(태그+클래스)을 가진 형제가 3개 이상이고 내용이 있는 그룹을 자동 감지하며, `detection`에 방법/서명/컨테이너 ID/후보 목록을 돌려줍니다.
- `fields` 생략 시 자동 필드: `title`, `url`, `price`, `image`, `text`(필수 후보; 없으면 `missing`)와 있을 때만 포함하는 `original_price`(취소선/원가 요소, 또는 할인율 표기가 있을 때 더 큰 가격), `discount`(%), `shipping`, `sold`(누적 판매량), `recent_sales`(기간 한정 구매량 `{raw, value, plus, period=month|week|day|year}`: "100+ bought in past month", "지난달 100개 이상 구매"; `sold`와 같은 수치로 취급하지 않음), `rating`(0~5; 평점/별점/★/점//5/"(리뷰 수)" 문맥 또는 `별 5개 중 4.3개`/`5점 만점에 4.3점` 같은 최댓값 우선 표현), `reviews`.
- 지정 필드 명세: `{이름: "선택자"}` 또는 `{이름: {selector?, source?, attr?, type=text|number|price|url|count|rating|sold|recent_sales|reviews|discount|shipping, all=false, regex?, relation?, closest?, canonical?, stable_id?}}`. `sold`/`recent_sales`/`reviews`/`discount`/`shipping`은 자동 필드와 같은 해석기를 지정 요소(또는 `source: "self"`로 카드 전체)에 적용해 작은 객체만 돌려줍니다.
  - `selector` 생략, `source: "self"`, `selector: ":self"`, `selector: ":scope"`는 레코드 요소 자체를 읽습니다(카드 전체 표시 텍스트).
  - `relation: "closest"`는 선택 요소에서 가장 가까운 조상(`closest`, 기본 `a[href]`)으로 대상을 바꿉니다. 조상이 레코드 밖이면 `relation_no_match`로 null입니다(다른 카드의 값을 가져오지 않음).
  - `type: "url"`에서 `attr`이 없으면 선택 요소의 href → 레코드 안의 가장 가까운 `a[href]` → 내부 첫 `a[href]` 순으로 찾고, 자기 href가 아니면 `record.sources[이름]`에 `closest_link`/`inner_link`를 남깁니다. `<a><h2>…</h2></a>`와 `<h2><a>…</a></h2>` 구조 모두 `{"selector": "h2", "type": "url"}`로 URL을 얻습니다.
  - `canonical: true`(`type: "url"` 전용)는 값을 `{raw, canonical, stable_id, removed}`로, `canonical: "compact"`는 `{canonical, stable_id}`만 돌려줍니다(추적 URL이 긴 사이트에서 레코드당 수백 바이트 절약). 알려진 추적 키(`ref`, `utm_*`, `qid`, `sr`, `sprefix`, `crid`, `dib`, `keywords`, `th`, `psc`, `spm`, `scm`, `algo_pvid`, `pd_rd_*`, `pf_rd_*`, `fbclid`, `gclid` 등)와 Amazon식 `/ref=…` 경로 조각, 프래그먼트만 제거하며 목록에 없는 쿼리(서명·인증용 포함)는 보존합니다. `stable_id`는 `stable_id` 정규식(첫 캡처 그룹)을 우선하고, 없으면 `/dp/<ASIN>`, `/item/<숫자>`, `/products/<숫자>`, `pcode=<숫자>` 같은 일반 패턴을 씁니다. `dedupe`는 이 객체를 `stable_id`(없으면 `canonical`)로 비교합니다.
  - `regex`의 첫 캡처 그룹(없으면 전체 일치)을 값으로 씁니다.
- 텍스트 조립 규칙: 블록 요소 경계는 공백, 인라인 경계는 앞 조각이 통화 기호·`.`·`,`로 끝나거나 뒤 조각이 `.`·`,`로 시작할 때만 붙입니다. 그래서 `<span>₩12,345</span><span>1,000+ 판매</span>`는 "₩12,345 1,000+ 판매", `<span>US $</span><span>12</span><span>.</span><span>34</span>`는 "US $12.34"입니다. 통화 표기는 ISO 코드(KRW/USD/EUR/GBP/JPY/CNY/CAD/AUD)로 통일합니다.
- 레코드: `{index, id, fields, missing?, sources?, explain?}`. `missing`은 필드별 누락 사유(`selector_no_match`, `relation_no_match`, `attribute_empty`, `text_empty`, `regex_no_match`, `parse_failed`=숫자가 없는 원문, `unsupported_locale_pattern`=숫자는 있지만 해석기가 모르는 표현)이며 누락 값은 `null`입니다. `explain=true`면 필드마다 `{selector_matches, raw(200자), parse_status}`를 `explain`에 돌려줍니다(기본 응답 크기는 그대로; raw에는 비밀번호가 가려진 표시 텍스트/속성만).
- `dedupe`: `true`(url이 하나라도 있으면 url, 없으면 title+price) 또는 필드 배열. 키가 모두 null/빈 값인 레코드는 비교에서 제외하고 **보존**합니다. 응답 `dedupe{keys, usable_records, skipped_records, duplicates_removed, key_coverage{키: 원본 대비 유효값 비율}, warning?}`로 "URL 선택자가 틀려 전부 null"인 상황을 바로 알 수 있습니다. 복합 키는 유효한 값의 조합으로 비교합니다. `text`/`match`로 레코드 텍스트 조건을 걸 수 있습니다.
- `quality`: `{confidence: high|medium|low|none, coverage: {필드: 출력 레코드 중 값이 있는 비율}, records, source_records, output_records, deduplicated, duplicate_ratio, dedupe_key_coverage?, missing?{필드: {사유: 수}}, warnings?, hint?}`. 자동 감지에서는 title/url/price 커버리지 평균이 0.8 이상이면 `high`입니다. 다음 경우 경고를 남기고 `high`를 돌려주지 않습니다: 제거율 50% 이상, dedupe 키 커버리지 50% 미만, 핵심 필드가 출력 레코드 전체에서 같은 값, 어떤 레코드에서도 요소를 찾지 못한 지정 필드. `low`/`none`이거나 선택자가 전부 실패하면 `hint`가 `container`/`item`/`fields`/`source: "self"`/`relation`/`explain` 사용을 안내합니다.
- 응답: `records`, `total`, `matched`, `duplicates_removed`, `dedupe?`, `detection`, `quality`, `offset`, `next_offset`, `omitted`, `missing_fields`, `frame`, `budget`.

```json
{"records":[{"index":0,"id":"t1e20","fields":{"title":"Valeton Rushead Max","url":{"raw":"https://www.amazon.com/…/dp/B07W3K92ZM/ref=sr_1_1?dib=…&qid=…&sr=8-1","canonical":"https://www.amazon.com/…/dp/B07W3K92ZM","stable_id":"B07W3K92ZM","removed":["/ref=","dib","qid","sr"]},
   "rating":{"raw":"별 5개 중 4.3개","value":4.3},"recent":"… 지난달 100개 이상 구매 …"},"sources":{"url":"closest_link"}}],
 "total":48,"matched":47,"duplicates_removed":1,"dedupe":{"keys":["url"],"usable_records":48,"skipped_records":0,"duplicates_removed":1,"key_coverage":{"url":1}},
 "quality":{"confidence":"high","coverage":{"title":1,"url":1,"rating":0.98,"recent":1},"records":47,"source_records":48,"output_records":47,"deduplicated":1,"duplicate_ratio":0.021,"dedupe_key_coverage":{"url":1}}}
```

### 상품 상세 요약(`page.product`)

상세 페이지 하나를 사이트별 선택자 없이 구조화합니다. 신뢰도 순으로 JSON-LD(schema.org Product/Offer/AggregateRating) → 마이크로데이터(`itemprop`) → Open Graph/`product:price:*` 메타 → DOM 휴리스틱(h1, 글자가 가장 큰 가격 요소, 취소선 원가, 본문의 판매량/평점/리뷰/배송/재고 문구, 판매자, 옵션 select와 sku/option 그룹, 큰 이미지)을 합칩니다.

```json
{"detected":true,"confidence":"high","url":"https://…","structured_data":{"json_ld":true,"microdata":false,"open_graph":true},
 "product":{"title":"무선 이어폰 A","price":{"raw":"12345 KRW","value":12345,"currency":"KRW"},"original_price":{"raw":"15,000원","value":15000,"currency":"KRW"},
            "discount":18,"sold":{"raw":"1,000+","value":1000,"plus":true},"rating":4.5,"reviews":{"raw":"1234","value":1234,"plus":false},
            "shipping":"무료배송","availability":"in_stock","brand":"아리","seller":"아리스토어","sku":"A-1",
            "options":[{"label":"색상","id":"t1e40","choices":2,"selected":"검정"}],"images":["https://…/one.png"],"description":"…","title_id":"t1e12","price_id":"t1e15"},
 "sources":{"title":"json-ld","price":"json-ld","original_price":"dom","discount":"text","sold":"text","rating":"json-ld","reviews":"json-ld","shipping":"text"}}
```

응답에는 `recent_sales`(기간 한정 구매량), `canonical_url`/`stable_id`/`canonical_source`(`link_canonical`이면 페이지가 선언한 `<link rel="canonical">`, `url`이면 현재 URL에서 추적 매개변수를 제거한 값)도 있습니다. `detected`는 제목과 가격을 모두 찾았는지, `confidence`는 가격 출처가 구조화 데이터/메타면 `high`, DOM 휴리스틱이면 `medium`입니다. 원가는 취소선 요소에서만 읽어 다른 판매처 가격을 원가로 오인하지 않습니다. `og:title`이 사이트 이름과 같거나 `og:type=website`면 h1을 제목으로 우선합니다. 찾지 못하면 `detected=false`와 `hint`를 돌려줍니다.

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

`doctor`(또는 CLI `--doctor`)는 `aribrowser`, `platform`, `user{name,uid,is_root}`, `display{DISPLAY,WAYLAND_DISPLAY,XAUTHORITY,xauthority_exists}`, `chromium{chromium,headless_shell,launch_path,browsers_root,bundled,version}`, `browser_policy`, `system_browsers[{kind,path,resolved,snap}]`, `apparmor{…,profile_roots,profile_covers_current}`, `userns`, `sandbox_helper{installed,path,owner_uid,mode}`, `suid_sandbox`, `launch_env`, `sandbox{ok,sandbox_active,sandbox_source,code,message,fix,seccomp,fallback?}`, `profiles`(잠금 상태), `processes`, `checks[{id,status=ok|warn|info|fail,code,message}]`, `summary{ok,error_code,fix,warnings,system_browser_fallback}`를 돌려줍니다. `--browser auto`에서 번들 실행이 샌드박스 문제로 실패하면 시스템 브라우저로 같은 검사를 반복해 `sandbox.fallback`에 담고 `sandbox_launch`를 경고로 낮춥니다. 프로토콜 `doctor`는 `session{open_profiles,tabs,own_processes,foreign_processes}`를 추가합니다. 진단 코드와 조치는 [SANDBOX.md](SANDBOX.md)에 있습니다.

`close` 응답:

```json
{"closed":true,"profiles":[{"closed":"default","processes":{"before":6,"exited":6,"forced":0,"remaining":[]},"lock":{"released":true,"path":null,"removed":[],"remaining":false}}],
 "processes":{"terminated":6,"forced":0,"remaining":0,"remaining_pids":[]},"locks":{"released":true,"remaining":[]}}
```

## 이벤트와 오류

이벤트는 seq/time/type과 tab/profile 등의 문맥을 갖습니다. `loading_started`, `dom_ready`, `loading_finished`, `navigation`, `frame_navigation`, `redirect`, `navigation_retry`, `tab_created`, `tab_closed`, `popup_created`, `dialog_opened`, `dialog_closed`, `download_started`, `download_completed`, `download_failed`, `network_failed`, `not_found`, `authentication_required`, `server_error`, `http_error`, `script_error`, `browser_crashed`, `browser_fallback`, `browser_selected`, `profile_closed`, `profile_lock_recovered`, `profile_settings_ignored`, `background_error`를 제공합니다. 버퍼 초과는 `events_omitted=true`로 표시합니다.

주요 오류 코드는 `INVALID_JSON`, `INVALID_REQUEST`, `INVALID_ARGUMENT`, `UNKNOWN_COMMAND`, `TAB_NOT_FOUND`, `PROFILE_NOT_FOUND`, `PROFILE_EXISTS`, `PROFILE_LOCKED`, `ELEMENT_NOT_FOUND`, `STALE_ELEMENT`, `ELEMENT_NOT_ACTIONABLE`, `ACTION_FAILED`, `EXPECTATION_NOT_MET`, `NETWORK_FAILED`, `BROWSER_UNAVAILABLE`, `BROWSER_CRASHED`, `BROWSER_ERROR`, `BROWSER_LAUNCH_FAILED`, `PAGE_UNRESPONSIVE`, `WAIT_TIMEOUT`, `PAGE_EMPTY`, `PAGE_BLOCKED`, `FILE_NOT_FOUND`, `RESOURCE_NOT_FOUND`, `RESOURCE_FAILED`, `IO_ERROR`, `SESSION_CLOSED`와 실행 환경 코드 `CHROMIUM_MISSING`, `CHROMIUM_DEPENDENCIES_MISSING`, `SANDBOX_ROOT_USER`, `SANDBOX_USERNS_RESTRICTED`, `SANDBOX_HELPER_INVALID`, `SANDBOX_PROFILE_STALE`, `SANDBOX_PROFILE_NOT_LOADED`, `SANDBOX_LAUNCH_FAILED`, `SYSTEM_BROWSER_MISSING`, `DISPLAY_MISSING`입니다. HTTP 404/500은 렌더 가능한 문서이므로 탐색은 성공 응답이며 `page.http_status`, 요약의 `errors.http_status`와 이벤트에 오류를 표시합니다.
