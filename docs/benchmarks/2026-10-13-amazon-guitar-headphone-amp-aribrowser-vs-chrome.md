# AriBrowser 0.2.0 vs Chrome 146 — Amazon 일렉기타 헤드폰 앰프 검색 비교

**측정 시각:** 2026-10-13 15:51 UTC  
**관점:** 사람의 UI 사용성이 아닌 AI Agent의 작업 완수성·관측 비용·자동화 복잡도

## 결론

두 방식 모두 Amazon 검색 결과 2페이지에서 96개 레코드를 수집했고, ASIN 96개가 전부 일치했다. 관련 상품을 추려 후기 수 내림차순, 동률이면 평점 내림차순으로 정렬한 상위 10개의 ASIN과 순서도 완전히 일치했다.

- **속도:** Chrome 승. 6.929초로 AriBrowser 7.915초보다 0.987초, 12.47% 빨랐다.
- **Agent 관측 토큰:** AriBrowser 근소한 승. `o200k_base` 프록시 기준 45,970 대 47,653으로 1,683토큰, 3.53% 적었다.
- **정확성:** 동률. 96/96 ASIN 일치, 상위 10개와 순서 모두 일치.
- **Agent 구현 편의성:** AriBrowser 승. JSONL 구조화 추출, readiness, 페이지 상태, 응답 예산, 검증 기능이 기본 제공된다. Chrome은 Playwright와 사이트별 JavaScript, 대기·정규화·중복 제거·품질 검증을 Agent가 직접 구현해야 했다.
- **종합 판단:** 이번 작업에서는 **AriBrowser가 AI Agent 관점의 근소한 종합 승자**다. 약 1초 느리지만 결과 품질이 같고 관측 토큰이 적으며 제어 계약이 더 안전하고 명시적이다. 단, Amazon 전용 DOM 추출 코드를 이미 보유한 반복 작업이라면 Chrome이 더 빠른 선택이다.

## 설치 및 엔진 검증

- 저장소: `zzabisagent-lab/AriBrowser`
- 확인한 저장소 HEAD: `908b3e4fa6c0b7c8006d78d51ceb7bb7238dc3e7`
- 최신 GitHub Release: `v0.2.0`, 게시 시각 `2026-10-13T09:22:44Z`
- 설치 파일: `aribrowser-0.2.0-linux-x64.tar.gz`
- SHA-256 검증: `613550e2dd670b3c6a722b3d9edc643acd99220a1c4e0fd30a5e22b2963a9965`
- 설치된 AriBrowser: `0.2.0`
- Chrome: `146.0.7680.80`, `/opt/google/chrome/chrome`
- AriBrowser 번들 Chromium 153은 호스트의 AppArmor 비특권 user namespace 제한 때문에 직접 시작하지 못했다. `--browser auto`가 샌드박스가 활성화된 시스템 Chrome 146으로 안전하게 대체했고 doctor 검사를 통과했다.
- 따라서 이 결과는 브라우저 엔진 비교가 아니라 **동일 Chrome 엔진 위의 Agent 인터페이스·워크플로 비교**다.

## 고정한 검색 계약

- 검색어: `electric guitar headphone amp`
- 대상: Amazon.com 검색 결과 1~2페이지
- 두 실행은 독립 프로필을 사용하고 `asyncio.gather` 장벽에서 동시에 시작
- 각 실행의 레코드 예산: 페이지당 최대 60개
- 관련 상품: 상품명에 guitar/기타와 headphone amp/headphone amplifier/헤드폰 앰프가 함께 있는 상품
- 정렬: 후기 수 내림차순, 동률이면 평점 내림차순, 그다음 공개된 최근 월 구매 배지
- Amazon은 전체 판매량을 공개하지 않으므로 판매량 순위를 단정하지 않았다. `N+ bought in past month` 배지는 표시된 카드에만 보조 정보로 수집했다.
- 비교 키: Amazon ASIN

## 상위 10개

1. **Valeton Rushead Max** — 평점 4.5, 후기 3,900, 최근 월 구매 표시 100+, 표시가 KRW 45,524  
   https://www.amazon.com/dp/B07W3K92ZM
2. **SONICAKE Mini Guitar Headphone Amp — US Classic** — 평점 4.1, 후기 2,700, 최근 월 구매 표시 50+, KRW 40,226  
   https://www.amazon.com/dp/B079FHY8PV
3. **SONICAKE US Madness** — 평점 4.0, 후기 1,400, KRW 37,543  
   https://www.amazon.com/dp/B079FGN89H
4. **NUX Mighty Plug MP-2** — 평점 4.5, 후기 1,300, 검색 카드 가격 미표시  
   https://www.amazon.com/dp/B084JB519R
5. **Donner Mini Guitar Headphone Amp — Heavy Metal** — 평점 4.2, 후기 1,200, 최근 월 구매 표시 50+, KRW 45,591  
   https://www.amazon.com/dp/B079FWQV3Z
6. **LEKATO Micro Guitar Headphone Amp with Multi Effects** — 평점 4.2, 후기 1,100, KRW 45,859  
   https://www.amazon.com/dp/B098JTSJTH
7. **Donner Mini Bass Guitar Headphone Amp — Wah-Basement** — 평점 4.1, 후기 1,100, KRW 40,226  
   https://www.amazon.com/dp/B079G6Y5Y5
8. **Donner Classic Rock Mini Guitar Headphone Amp** — 평점 4.1, 후기 735, KRW 33,519  
   https://www.amazon.com/dp/B01FHM16SU
9. **Positive Grid Spark NEO Wireless Smart Guitar Headphone Amp & Rig** — 평점 4.4, 후기 633, 최근 월 구매 표시 300+, KRW 266,921  
   https://www.amazon.com/dp/B0DTPMLMFV
10. **LEKATO Guitar Headphone Amp, 5 Effects** — 평점 4.3, 후기 616, 최근 월 구매 표시 400+, KRW 53,639  
    https://www.amazon.com/dp/B09TKK1ZFM

가격·후기·평점·월 구매 표시는 Amazon의 시점·지역·로그인·재고에 따라 바뀔 수 있다.

## 성능과 품질

### AriBrowser 0.2.0

- 작업 시간: 7.915초
- 수집 레코드: 96개
- Agent-visible 관측: 100,468바이트
- `o200k_base` 프록시 토큰: 45,970
- 페이지 성공 판정: 2/2

### Chrome 146 + Playwright targeted DOM

- 작업 시간: 6.929초
- 수집 레코드: 96개
- Agent-visible 관측: 109,444바이트
- `o200k_base` 프록시 토큰: 47,653
- 페이지 성공 판정: 2/2

### 일치도

- 전체 ASIN 교집합: 96/96, 100%
- 상위 10개 ASIN 교집합: 10/10
- 상위 10개 순서: 완전 일치

## AI Agent 관점의 편의성과 효율성

### AriBrowser 장점

- 표준입출력 JSONL이라 별도 브라우저 SDK 객체 모델 없이 프로세스 하나로 제어할 수 있다.
- `extract`가 상품 카드 경계를 유지해 제목·가격·평점·후기를 한 레코드로 반환한다.
- `readiness`가 빈 SPA 껍데기, 차단, 로그인 페이지를 정상 탐색으로 오인할 위험을 줄인다.
- `page.state`, `budget`, 짧은 요소 ID, 조작 검증, sandbox doctor가 Agent의 상태 추론 부담을 줄인다.
- 임의 JavaScript 실행 대신 제한된 selector/field 계약을 사용하므로 제어면이 더 작고 재현성이 좋다.
- 이번 동일 품질 실행에서 관측 토큰과 바이트가 Chrome보다 각각 3.53%, 8.20% 적었다.

### AriBrowser 마찰점

- 현재 호스트에서는 최신 번들 Chromium을 쓰려면 1회 sandbox helper 설치가 필요했고, 테스트에서는 시스템 Chrome fallback을 사용했다.
- Amazon이 AriBrowser 세션에는 한국어 상품명을 제공해, 평점 문자열 `별 5개 중 4.3개`를 Agent가 정규화해야 했다. `rating` 자동 파서는 이 로캘 문자열을 바로 수치화하지 못해 `text`로 읽고 변환했다.
- Amazon 카드의 최근 월 구매 배지는 이 추출 계약에서 직접 얻지 못했다. Chrome의 전체 카드 텍스트에서는 얻을 수 있었다.
- `navigate` 요약과 페이지 상태가 기본적으로 포함되어, 이미 사이트별 최소 selector를 아는 Agent에게는 일부 고정 오버헤드가 된다.

### Chrome 장점

- 가장 빠르며 시스템에 이미 설치되어 추가 368MB 배포물 설치가 필요 없었다.
- JavaScript로 카드 내부 텍스트를 한 번에 읽어 최근 월 구매 배지를 함께 수집했다.
- 사이트 DOM을 정확히 아는 코딩 Agent는 필요한 필드만 자유롭게 최적화할 수 있다.

### Chrome 마찰점

- Playwright 설치, 브라우저 context, 대기 조건, selector, JavaScript 직렬화, URL 정규화, ASIN 추출, 중복 제거, 로캘별 숫자 파싱, 품질 검증을 모두 Agent가 작성해야 했다.
- 구조화된 페이지 readiness, action verification, 토큰/바이트 budget, page-state 계약이 기본 제공되지 않는다.
- 임의 JavaScript는 강력하지만 유지보수 범위와 공격 표면이 더 넓고, Amazon DOM 변경 시 Agent 코드가 직접 깨진다.

## 토큰 측정 주의사항

정확한 모델 제공자 API 토큰은 이 공유 Hermes 세션에서 두 브라우저 실행별로 격리할 수 없었다. 따라서 보고한 수치는 저장된 **Agent-visible JSON 관측 payload**를 동일한 `o200k_base` tokenizer로 센 프록시이며, 청구 토큰이 아니다. AriBrowser의 내장 `heuristic-v1` 추정치와도 섞지 않았다.

비교는 인터페이스 전략까지 포함한다. AriBrowser는 네이티브 구조화 추출과 페이지 요약을 반환했고, Chrome은 브라우저 안에서 사이트별 JavaScript로 DOM을 선별했다. 따라서 3.53% 차이를 순수 엔진 효율 차이로 해석하면 안 된다.
