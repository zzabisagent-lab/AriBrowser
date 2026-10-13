# 버전 이력

## 0.3.0 — 2026-10-14

Amazon 상품 검색 동시 벤치마크(`docs/benchmarks/2026-10-13-amazon-guitar-headphone-amp-aribrowser-vs-chrome.md`: AriBrowser 0.2.0 대 Chrome 146, 96개 ASIN·상위 10개 순위 완전 일치)에서 드러난 **정확성·로캘·구조화 추출·토큰 효율** 문제를 고친 릴리스입니다. 요구사항 문서의 v0.2.1 긴급 수정(null 중복 키, 한국어 평점, 레코드 자체 필드, 최근 월 구매량, 품질 경고)과 v0.3.0 기능(프로필 로캘/시간대, minimal 탐색, 관계 기반 URL, explain 모드, 실제 실행 엔진 정보)을 모두 포함하고, 후속 항목이던 canonical URL/안정 식별자와 Linux `.deb` 패키지도 넣었습니다. 기존 `query`/`extract`/`summary` 계약은 유지되며 달라진 값은 `docs/PROTOCOL.md`의 "0.3.0에서 달라진 기본 동작"에 정리했습니다. 목표는 변하지 않았습니다: AI Agent가 **더 적은 토큰으로 더 빨리** 웹 검색 목표를 달성하고, 결과 손실을 조용히 넘기지 않는 것입니다.

### 실측 효과

같은 호스트(Ubuntu 24.04, 번들 Chromium 153 + 공용 샌드박스 도우미, `locale=en-US`)에서 Amazon 검색 "electric guitar headphone amp" 결과 1페이지(48개 카드)를 같은 추출 계약(제목, canonical URL, 가격, 평점, 리뷰 수, 최근 월 구매량)으로 읽은 Agent-visible payload입니다. 토큰은 벤치마크와 같은 `o200k_base` 프록시입니다.

| 흐름 | 왕복 | 탐색 응답 오버헤드(레코드·상태 제외) | 전체 payload | 시간 |
| --- | --- | --- | --- | --- |
| 0.3.0 `navigate`(summary) + `extract` + `page.state` | 3회 | 5,743바이트 | 35,086바이트 / 10,721토큰 | 4.37초 |
| 0.3.0 `navigate(mode="minimal", include=["records","state"])` | 1회 | 1,226바이트 (−79%) | 30,650바이트 / 9,409토큰 (−12%) | 3.64초 (−17%) |
| 같은 계약에 `canonical: true`(raw·removed 포함) | 1회 | 1,227바이트 | 59,259바이트 / 25,285토큰 | 3.64초 |

- 48개 레코드 기준 9,409토큰은 벤치마크의 2페이지 96개 레코드로 환산하면 약 18,800토큰으로, 0.2.0 벤치마크의 45,970토큰 대비 59% 적습니다(벤치마크 Agent의 추출 계약과 완전히 같지는 않으므로 참고치입니다). 레코드 자체가 payload의 대부분이므로 긴 추적 URL은 `canonical: "compact"`로, 카드 전체 텍스트 대신 `{source: "self", type: "recent_sales"}`처럼 해석기 type을 쓰는 것이 가장 큰 절감입니다.
- 측정 중 Amazon이 2페이지 요청에 "Continued access by an unauthorized AI agent violates Amazon's Conditions of Use" 안내 페이지(제목 `Amazon.com`, Continue 버튼)를 돌려준 경우가 있었습니다. 0.2.0은 이 페이지를 `ready`로 보고하고 `extract`가 0건을 돌려줬지만 0.3.0은 `blocked`(`blocked_text`)로 분류해 `PAGE_BLOCKED` 오류를 돌려줍니다. AriBrowser는 이런 안내를 우회하거나 버튼을 자동으로 누르지 않습니다.
- 로컬 테스트 사이트의 Amazon형 페이지(카드 12개)에서는 minimal 응답의 요약 오버헤드가 summary 모드의 절반 미만입니다(`tests/test_v030.py`). 같은 페이지를 0.2.0 바이너리로 읽으면 `{selector: ":scope"}`가 null, `별 5개 중 4.3개`가 null, `5점 만점에 4.0점`은 5로 잘못 해석됐고, 0.3.0은 각각 카드 전체 텍스트, 4.3, 4.0을 돌려줍니다.

### P0 — 결과 정확성과 재현성

- **프로필 로캘·시간대 고정(P0-1)**: `profile.create`에 `locale`(BCP 47)과 `timezone_id`(IANA)를 추가했습니다. Playwright 영구 컨텍스트에 연결되어 `Accept-Language`, `navigator.language`, `Intl` 시간대가 실행 환경이 아니라 프로필 설정을 따릅니다. 적용값은 `profile.create.settings`, `profile.list`, `capabilities.profiles`에 돌려주고 프로필 디렉터리의 `aribrowser-profile.json`에 저장해 같은 프로필을 다시 열 때(옵션 생략) 유지합니다. 잘못된 태그/ID는 `INVALID_ARGUMENT`이며 tz 데이터가 없는 호스트를 위해 `tzdata`를 의존성에 넣고 번들에 포함했습니다. CLI `--locale`/`--timezone`으로 세션 기본값을 둘 수 있습니다.
- **한국어 평점 해석(P0-2)**: `parseRating`이 최댓값이 먼저 나오는 `별 5개 중 4.3개`, `5점 만점에 4.3점`, `5개 중 4.3`을 기존 `4.3 out of 5 stars`, `4.3/5`와 같은 `{raw, value}`로 돌려줍니다. "5개 중 3개 남음" 같은 재고 문구는 소수 평점이 아니면 제외하고, 모델명·치수·가격은 계속 배제합니다. 자동 필드, `query(fields=["rating"])`, `extract(type="rating")`이 같은 해석기를 씁니다.
- **null 중복 키에 안전한 dedupe(P0-3)**: 지정한 dedupe 키가 모두 null/빈 값인 레코드는 비교 대상에서 빼고 **보존**합니다. 0.2.0에서는 URL 선택자가 틀리면 키가 `[null]`로 같아져 48개 카드가 1개로 줄었습니다. 응답의 `dedupe{keys, usable_records, skipped_records, duplicates_removed, key_coverage, warning}`로 키 커버리지를 알리고, 복합 키는 유효한 조합으로 비교하며, `canonical=true` URL은 안정 식별자(없으면 canonical URL)로 비교해 추적 매개변수만 다른 같은 상품을 하나로 봅니다. `query`의 dedupe도 같은 규칙입니다.
- **dedupe 전후를 드러내는 quality(P0-4)**: `quality`에 `source_records`, `output_records`, `deduplicated`, `duplicate_ratio`, `dedupe_key_coverage`, `missing{필드: {사유: 수}}`, `warnings`를 추가했습니다. 제거율 50% 이상, 키 커버리지 50% 미만, 핵심 필드가 모든 레코드에서 같은 값, 어떤 레코드에서도 요소를 못 찾은 필드는 경고를 남기고 `high`를 돌려주지 않습니다.

### P1 — Agent 효율과 API 사용성

- **레코드 자체 필드(P1-1)**: `{source: "self"}`, `{selector: ":self"}`, 호환을 위한 `{selector: ":scope"}`가 카드 전체 표시 텍스트를 읽습니다(0.2.0은 `:scope`가 null이었습니다). 선택자 생략도 그대로 지원합니다.
- **최근 월 구매량 분리(P1-2)**: 자동 필드 `recent_sales{raw, value, plus, period}`가 `100+ bought in past month`, `지난달 100개 이상 구매`, `최근 한 달간 100개 이상 구매`, `월 100개 이상 판매` 등을 읽고, 누적 `sold`는 이런 기간 표현을 더는 판매량으로 오인하지 않습니다. `query.fields`에 `recent_sales`, `page.product`에도 같은 필드를 추가했습니다. 지정 필드 `type`에 `sold`/`recent_sales`/`reviews`/`discount`/`shipping`을 추가해 `{source: "self", type: "recent_sales"}`처럼 카드 전체 텍스트를 돌려받지 않고 해석 결과만 받을 수 있습니다.
- **minimal 탐색(P1-3)**: `navigate(mode="minimal")`은 관측(DOM 수집·요약)을 생략하고 `page{url,title,http_status}`, `readiness`(ready가 아닐 때만 signals), `verification`, 요청한 `include` 결과, `errors`(있을 때만), `runtime`, `budget`, `cursor`만 돌려줍니다. 영역/제목/역할 수/검색창/활동 집계는 싣지 않지만 `PAGE_EMPTY`/`PAGE_BLOCKED` 판정과 HTTP 오류 보고는 그대로입니다. 관측을 건너뛰므로 시간도 줄어듭니다.
- **관계 기반 URL 필드(P1-4)**: `{selector: "h2", type: "url", relation: "closest", closest: "a[href]"}`가 제목을 감싸는 링크를 읽고, `type: "url"`에서 선택 요소에 href가 없으면 자기 href → 레코드 안의 가장 가까운 `a[href]` → 내부 첫 `a[href]` 순으로 자동 탐색해 출처를 `record.sources`(`closest_link`/`inner_link`)에 남깁니다. 관계 탐색은 레코드 경계를 벗어나면 `relation_no_match`로 null을 돌려주어 다른 카드의 링크를 가져오지 않습니다.
- **canonical URL과 안정 식별자(P1-5)**: `{type: "url", canonical: true, stable_id?: "정규식"}`이 `{raw, canonical, stable_id, removed}`를, `canonical: "compact"`는 `{canonical, stable_id}`만 돌려줍니다. 알려진 추적 키(`ref`, `utm_*`, `qid`, `sr`, `dib`, `spm`, `algo_pvid`, `pd_rd_*`, `pf_rd_*`, `fbclid`, `gclid` 등)와 Amazon식 `/ref=…` 경로 조각, 프래그먼트만 제거하고 서명·인증용 쿼리는 보존합니다(보수적 기본값). 안정 식별자는 호출자의 정규식(첫 캡처 그룹)을 우선하고 없으면 ASIN(`/dp/<10자>`)·숫자 상품 번호(`/item/<n>`, `pcode=<n>` 등)를 씁니다. `page.product`는 `<link rel="canonical">`을 우선한 `canonical_url`/`stable_id`/`canonical_source`를 돌려줍니다.

### P2 — 배포·진단·개발 경험

- **실행 엔진 정보 `runtime`(P2-1)**: 첫 `profile.create`/`navigate` 응답과 `capabilities.runtime`에 `{runtime_id, kind, product, version, executable, resolved_executable, fallback_from, sandbox_active, sandbox_source, sandbox_evidence, headless, user_agent}`를 돌려줍니다. 버전은 CDP `Browser.getVersion`의 제품 문자열에서, 실행 파일은 Linux에서 브라우저 주 프로세스의 `/proc/<pid>/exe`에서 읽어 launcher 이름으로 추정하지 않습니다. 이후 응답은 `{runtime_id}`만 싣습니다.
- **Linux `.deb` 패키지(P2-2)**: 빌드가 `aribrowser_0.3.0_amd64.deb`를 함께 만듭니다. `/opt/aribrowser`에 설치하고 `/usr/bin/aribrowser` 링크를 두며, `postinst`가 공용 setuid 샌드박스 도우미를 설치하고 `prerm`(remove/purge)이 AriBrowser가 설치한 도우미·정책만 제거합니다. Ubuntu 24.04에서 전역 AppArmor/sysctl을 바꾸지 않고 번들 Chromium `--self-test`가 샌드박스 상태로 통과하며, 업그레이드해도 설정이 유지됩니다. CI의 Ubuntu 24.04 작업이 설치 → 자체 검사 → `runtime.resolved_executable=/opt/aribrowser/…` 확인 → 제거 후 잔여 없음을 검증합니다. `--no-sandbox` 자동 대체 금지는 그대로입니다.
- **explain 모드와 누락 사유 세분화(P2-3)**: `extract(explain=true)`가 레코드마다 `explain{필드: {selector_matches, raw(200자), parse_status}}`를 돌려줍니다. `missing` 사유는 `selector_no_match`, `relation_no_match`, `attribute_empty`, `text_empty`, `regex_no_match`, `parse_failed`(숫자 없음), `unsupported_locale_pattern`(숫자는 있지만 해석기가 모르는 표현)으로 나뉘고 `quality.missing`이 필드별로 집계합니다. 기본 응답 크기는 그대로이며 raw에는 비밀번호 값이 가려진 표시 텍스트/속성만 들어갑니다.

### 그 밖의 변경

- `capabilities`에 `runtime`, `profiles`, `profile_settings`, `navigate_modes`, `record_field_spec`, `record_field_types`, `query_fields`, `missing_reasons`를 추가하고 `record_auto_fields`에 `recent_sales`를 넣었습니다. `profile.list`가 적용 설정을 돌려줍니다.
- 이벤트 `profile_settings_ignored`(손상된 설정 파일 무시)를 추가했습니다.
- 번들 바이너리는 호스트의 Playwright 캐시나 `PLAYWRIGHT_BROWSERS_PATH`와 무관하게 자신에게 포함된 Chromium을 씁니다(브라우저 디렉터리를 절대 경로로 고정하고, 그래도 번들 밖 경로가 선택되면 번들 안의 같은 바이너리로 바꾸며 `browser_rebased` 이벤트를 남김). 다른 실행 파일은 `--chromium`으로 지정합니다. 이전에는 사용자 캐시의 같은 리비전이 설치 경로 대신 실행될 수 있었습니다.
- 테스트 사이트에 Amazon 한국어 결과를 본뜬 `/amazon`(링크가 h2를 감싸는 카드, 최댓값 우선 평점, 최근 월 구매 배지, 추적 매개변수 URL, 링크 없는 카드)과 로캘/시간대 확인용 `/echo`를 추가하고 `tests/test_v030.py`에 회귀시험을 넣었습니다.
- 문서: `docs/PROTOCOL.md`(0.3.0 기본 동작, 필드 명세, dedupe/quality, minimal, runtime), `docs/FEATURES.md`, `docs/SANDBOX.md`(.deb), README를 갱신했습니다.

## 0.2.0 — 2026-10-13

AI Agent가 **더 적은 토큰으로 더 빨리** 웹 검색 목표를 달성하도록, 실사용 비교(`docs/benchmarks/2026-10-13-aliexpress-aribrowser-vs-chrome.md`: AriBrowser 0.1.0 대 Chrome 직접 제어)에서 드러난 네 가지 문제를 고치고 Chrome 방식의 장점(설치 없이 바로 사용, 선택적 DOM 추출)을 가져온 릴리스입니다. 0.1.0의 장점(JSONL 인터페이스, 상품 카드 자동 탐지, 가격·URL·정렬 상태 기본 제공, 짧은 요소 ID와 예산, 명확한 진단)은 그대로 유지하며, 사이트별 선택자나 가격·판매량 텍스트 후처리 코드를 Agent가 직접 쓰지 않아도 되게 했습니다. 기본 동작이 바뀐 항목은 `docs/PROTOCOL.md`의 "0.2.0에서 달라진 기본 동작"을 참고하세요.

### 실측 효과 (같은 호스트·같은 세션, 실제 사이트)

| 상황 | 0.1.0 방식 | 0.2.0 기본 |
| --- | --- | --- |
| 다나와 통합검색 "아크릴" `navigate` | 13,270바이트 / 약 3,346토큰 (이벤트 원문 9,554바이트 포함) | 4,562바이트 / 약 1,200토큰 (`activity` 집계) |
| 다나와 상품 상세 `navigate` | 19,610바이트 / 약 4,933토큰, 13.5초 (`load` 10초 대기 + AX 보강) | 4,323바이트 / 약 1,143토큰, 6.1초 (readiness 판정 + AX 생략 요약) |
| AliExpress 상품 상세 `navigate` | 8,982바이트, **성공으로 보고** (빈 껍데기 + 보안 문자 프레임) | 2,059바이트, `PAGE_BLOCKED` 오류 + 근거(`_____tmd_____/punish` 프레임)·안내 |
| 다나와 검색 `navigate` + 레코드 5건 + 정렬/페이지 상태 | 3회 왕복 | 1회 왕복 11,629바이트 / 약 3,108토큰 (`include=["records","state"]`) |
| 다나와 상품 상세 `page.product` | 해당 없음 | 800바이트 / 약 206토큰 (제목·브랜드·가격·평점·리뷰 수·배송비·이미지, 출처 표시) |
| 다나와 상세 페이지 관측(`observe`) | 5.5초 (수집 1.0초 + AX 보강 4.5초) | 요약 모드 1.0초 (AX 생략), 상세 모드는 그대로 |

토큰은 외부 tokenizer 없이 문자 종류별 가중치로 추정한 값(`heuristic-v1`)이며 실제 모델과 다를 수 있습니다. 이벤트 수는 광고/추적 프레임 로딩 시점에 따라 달라지므로 같은 세션에서 연속 측정한 쌍만 비교했습니다.

### 1. 설치 경로와 무관한 샌드박스 — "새 설치 경로마다 AppArmor 정책 설치" 해소

- **공용 setuid 샌드박스 도우미(기본)**: `sudo ./aribrowser --install-sandbox`가 Chromium이 제공하는 `chrome_sandbox`를 고정 경로 `/usr/local/lib/aribrowser/chrome-sandbox`(root:root, 4755)에 **호스트당 한 번** 설치합니다. 모든 AriBrowser 실행은 이 도우미가 유효하면 `CHROME_DEVEL_SANDBOX`로 Chromium에 전달하므로 설치 경로, Chromium 리비전, 사용자와 무관하게 샌드박스가 동작합니다. AppArmor 유무, `kernel.apparmor_restrict_unprivileged_userns`, user namespace 제한에도 영향을 받지 않습니다(Google Chrome .deb가 쓰는 것과 같은 방식). 렌더러의 `Seccomp: 2`/`NoNewPrivs: 1`로 활성 근거를 확인합니다.
- **경로 독립 AppArmor 정책(대안)**: `--sandbox-method apparmor`의 정책이 특정 설치 경로 대신 모든 사용자의 Playwright 캐시(`/home/*/.cache/ms-playwright`, `/root/.cache/ms-playwright`)와 AriBrowser 배포 디렉터리 구조(`/**/_internal/playwright/driver/package/.local-browsers`)에 동시에 붙습니다. 새 경로에 풀어도 재설치가 필요 없으며, 0.1.0 바이너리도 이 정책 아래에서 그대로 동작합니다. 정책 머리글 `aribrowser-roots`와 글로브 해석으로 현재 경로 포함 여부를 판정합니다.
- **시스템 브라우저 자동 대체(`--browser auto`, 기본)**: 번들 Chromium이 샌드박스/의존성 문제로 실행되지 않고 Google Chrome/Chromium/Edge/Brave가 설치돼 있으면 그 브라우저로 실행합니다. 배포판이 이미 해당 브라우저에 userns를 허용하거나 자체 setuid 도우미를 갖추고 있어 **별도 설치 없이 바로 사용**할 수 있습니다. 대체는 `--no-sandbox`가 아니라 "샌드박스가 동작하는 다른 Chromium"으로 바꾸는 것이며 `browser_fallback`/`browser_selected` 이벤트와 `capabilities.browser`로 알립니다. `--browser bundled`/`system`으로 고정할 수 있고 Snap 패키지 Chromium은 홈의 숨김 디렉터리 접근 제한 때문에 후순위입니다.
- `--doctor`가 공용 도우미 상태(`sandbox_helper`), 설치된 시스템 브라우저 목록(`system_browsers`), 브라우저 정책, 대체 실행 검사 결과(`sandbox.fallback`)를 함께 돌려주고, 실제 실행이 성공하면 정적 AppArmor 판정을 참고 사항으로 낮춥니다. 새 코드 `SANDBOX_HELPER_INVALID`(도우미 손상), `SYSTEM_BROWSER_MISSING`. `--uninstall-sandbox`는 도우미와 정책을 함께 제거합니다. 어떤 경로에서도 `--no-sandbox`를 자동으로 사용하지 않는 원칙은 유지됩니다.

### 2. 탐색 응답의 네트워크 이벤트 URL 제거

- 관측 응답(`navigate`/`snapshot`/`find` 외 `action`/`wait`)이 이벤트 원문 배열 대신 `activity` 집계(`events` 수, `by_type` 종류별 개수, `redirects`, `frame_navigations`, `final_url`, 오류성 이벤트 최대 5건의 호스트+경로 축약 URL, `errors_total`, `since`/`cursor`)만 싣습니다. 광고/추적 iframe 탐색과 요청 실패 이벤트의 수백 자 URL이 응답의 절반 이상을 차지하던 문제가 사라졌습니다.
- 원문이 필요하면 `include_events=true`를 지정하거나 `events` 명령에 `since=activity.since`를 넘깁니다. 요약의 `errors.events`도 최근 3건·축약 URL·`event_counts`로 줄였고 경고 메시지는 120자로 자릅니다.

### 3. 빈 껍데기/차단 페이지를 성공으로 보고하지 않음 — 로딩 품질 검증

- `navigate`가 `load` 뒤에 readiness.js 신호(본문/내용 영역 텍스트 길이, SPA 루트 비어 있음, 스켈레톤/스피너, 보안 문자 프레임·스크립트·요소, 로그인 폼, 링크/입력/버튼 수, 구조화 데이터)를 모아 `ready`/`loading`/`empty`/`blocked`/`login_required`/`http_error`/`error`로 분류하고 `verification.content_ready`와 `readiness{status,reasons,kind,waited_ms,checks,attempts,signals,probe,hint}`를 돌려줍니다.
- 늦게 채워지는 SPA는 제한 시간(`ready.timeout_ms`, 기본 8초 이내) 안에서 DOM/네트워크가 조용해질 때까지 기다리고, 끝까지 빈 껍데기면 한 번 새로 고쳐(`ready.retry`, 기본 1) 다시 판정합니다. 그래도 `empty`면 `PAGE_EMPTY`, 보안 문자/차단 문구/HTTP 403·429면 `PAGE_BLOCKED` 오류로 근거(`readiness.signals.captcha` 등)와 다음 조치(`hint`)를 함께 돌려줍니다. 차단은 재시도하지 않습니다(차단 강화 방지). AliExpress 상세 페이지의 `_____tmd_____/punish` 보안 문자 프레임은 `blocked`로, 벤치마크에서 본 "제목 없는 껍데기"(머리글/바닥글 링크만 있고 앱 루트가 비어 있음)는 `no_title`/`text_below_min` 사유의 `empty`로 보고됩니다.
- `ready={min_text, selector, text, timeout_ms, retry, on_fail}`로 기준을 바꾸고(`on_fail=warn`이면 오류 대신 `content_ready=false` 성공 응답), `ready=false`로 검사를 생략할 수 있습니다. 작은 정적 문서(노드 수십 개, SPA 루트 없음)는 텍스트가 적어도 빈 껍데기로 보지 않으며, 로그인 페이지와 HTTP 오류 문서는 오류가 아닌 상태로 구분합니다.
- 검증이 켜져 있으면 `load`는 2초만 기다립니다. 광고/추적 자원 때문에 `load`가 10초 넘게 지연되는 페이지에서 본문이 완성된 즉시 응답합니다(다나와 상세 13.5초 → 6.1초).

### 4. 품질 확인·재시도 부담 축소 — 한 왕복에 더 많이, 더 작게

- `navigate.include=["records","product","state","events"]`와 `extract={...}`로 탐색 응답에 레코드 추출, 상품 요약, 정렬/페이지 상태를 함께 싣습니다. 새 `batch` 명령은 최대 20개 명령을 순차 실행해 결과 배열(`results`, `completed`, `failed`, `stopped_at`)을 돌려주며 기본으로 첫 실패에서 멈춥니다.
- 요약에 `kind`(listing/product/login/article/other)를 추가하고 `omitted.next` 추천 명령을 종류별로 바꿨습니다(목록은 `extract`→`page.state`, 상세는 `page.product`).
- `extract` 응답에 `quality{confidence=high|medium|low|none, coverage{필드: 비율}, records, hint}`를 추가해 자동 감지가 상품 목록을 제대로 잡았는지 재조회 없이 판단할 수 있습니다.
- 요약 모드(`navigate` 기본)는 Chromium AX 보강을 생략해 대형 페이지 관측을 수 초 단축합니다. 요약 기준선과 이후 AX 보강 관측의 이름/역할/상태 차이는 변경분으로 보고하지 않습니다.

### 5. 사이트별 선택자·후처리 없이 쓰는 추출 — Chrome 방식의 단점 보완

- **붙어 출력되는 텍스트 해결**: 요소 텍스트를 innerText 대신 textlib.js의 경계 규칙으로 조립합니다. 블록 경계는 공백, 인라인 경계는 앞 조각이 통화 기호·`.`·`,`로 끝나거나 뒤 조각이 `.`·`,`로 시작할 때만 붙입니다. `<span>₩12,345</span><span>1,000+ 판매</span>`는 "₩12,345 1,000+ 판매"로, `<span>US $</span><span>12</span><span>.</span><span>34</span>`는 "US $12.34"로 읽힙니다. `query`/`extract`의 `text` 필드와 모든 해석기가 이 텍스트를 씁니다.
- **자동 필드 확장**: 레코드 자동 필드에 `original_price`(취소선/원가 요소 또는 할인율이 있을 때 더 큰 가격), `discount`(%), `sold`(`{raw,value,plus}`: "1,000+ 판매", "sold 2.3K", "구매 345"; "판매 1위" 같은 순위는 제외), `rating`(평점/별점/★//5/점/"(리뷰 수)" 문맥이 있는 0~5만 인정해 "0.5x200mm" 같은 치수를 배제), `reviews`("리뷰수 ( 6 )", "1,234 reviews"), `shipping`을 추가했습니다. `query.fields`에 `sold`/`rating`/`reviews`/`discount`, 필드 명세 `type`에 `count`/`rating`을 추가했습니다. 가격 해석기는 `US $12.34`, `₩12,345`, `12,345 원`, `KRW 6200` 등 접두/접미 통화를 ISO 코드로 통일합니다.
- **상품 상세 요약 `page.product`**: JSON-LD(schema.org Product/Offer/AggregateRating) → 마이크로데이터 → Open Graph/`product:price` 메타 → DOM 휴리스틱(h1, 가장 큰 글자의 가격 요소, 취소선 원가, 판매량/평점/리뷰/배송/재고 문구, 판매자, 옵션 select/sku 그룹, 큰 이미지) 순으로 합쳐 `{title, price, original_price, discount, sold, rating, reviews, shipping, availability, brand, seller, sku, options, images, description}`과 필드별 출처(`sources`), `detected`/`confidence`, 제목/가격 요소 ID를 돌려줍니다. 다나와 상세는 JSON-LD로 800바이트에 요약됩니다.

### 그 밖의 변경

- `capabilities`에 `browser_policy`, `browser`(실제 실행 파일/대체 사유), `readiness_statuses`, `navigate_includes`, `record_auto_fields`를 추가했습니다.
- 이벤트 `navigation_retry`, `browser_fallback`, `browser_selected`를 추가했습니다. 오류 코드 `PAGE_EMPTY`, `PAGE_BLOCKED`, `SANDBOX_HELPER_INVALID`, `SYSTEM_BROWSER_MISSING`을 추가했습니다.
- CLI: `--browser auto|bundled|system`, `--sandbox-method auto|helper|apparmor|suid`(기본 auto=helper). 설치 검증은 번들 Chromium 자체의 샌드박스 실행을 확인합니다.
- GitHub Actions의 Ubuntu 24.04 작업이 공용 도우미 설치 → 다른 경로로 복사한 Chromium의 샌드박스 실행 → 도우미 제거 후 경로 독립 AppArmor 정책 설치 → 같은 경로의 실행을 순서대로 검증합니다.
- 테스트 사이트에 붙은 텍스트 상품 카드(`/market`), JSON-LD 상품 상세(`/product/1`), 늦게 채워지는 SPA(`/shell`), 빈 껍데기(`/shell-empty`), 보안 문자 프레임(`/blocked`), 403(`/forbidden`), 로그인 폼(`/login`) 페이지와 v0.2.0 통합 테스트(`tests/test_v020.py`)를 추가했습니다.
- 문서: `docs/PROTOCOL.md`(0.2.0 기본 동작, readiness/activity/include/batch/page.product), `docs/FEATURES.md`, `docs/SANDBOX.md`(공용 도우미·경로 독립 정책·시스템 브라우저), README를 갱신했습니다.

### 호환성

- 관측 응답에서 `events` 배열이 사라지고 `activity` 집계가 들어갑니다. 원문이 필요하면 `include_events=true` 또는 `events(since)`를 쓰세요. `cursor`는 유지됩니다.
- `navigate`는 빈 껍데기/차단 페이지에서 `PAGE_EMPTY`/`PAGE_BLOCKED` 오류를 돌려줍니다. 0.1.0 동작이 필요하면 `ready=false`(검사 생략) 또는 `ready={"on_fail":"warn"}`을 지정하세요. HTTP 404/500 문서는 여전히 성공 응답이며 `readiness.status=http_error`입니다.
- `extract` 자동 필드에 선택 필드가 추가됐고 `text`는 경계 규칙으로 조립한 문자열입니다. `extract` 응답에 `quality`가 추가됐습니다.
- `--install-sandbox`의 기본 방식이 AppArmor 정책에서 공용 setuid 도우미로 바뀌었습니다. 기존 0.1.0 정책은 그대로 유효하며 `--sandbox-method apparmor`로 경로 독립 정책으로 갱신할 수 있습니다.

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
