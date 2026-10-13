# AriBrowser 0.1.0 vs Chrome 146 — AliExpress benchmark

**Measured:** 2026-10-13 07:41 UTC

## Installation and engine verification

- Repository HEAD: `5ea278fa050601d4e4a469dbae8224a075a516ec`
- Release installed: AriBrowser `0.1.0`
- Launcher: `/home/hermes/.local/bin/aribrowser`
- Release archive SHA-256 verified: `2440bf7a8261a265c77618c1d5dcff80713c693ce880fc6323564009ac24b6e6`
- Bundled Chromium 153 could not start sandboxed because the new install path did not have the required root-installed AppArmor policy and passwordless sudo was unavailable.
- Safe fallback used: AriBrowser `--chromium /opt/google/chrome/chrome`; doctor and self-test passed with sandbox active, renderer seccomp filtering, and Google Chrome `146.0.7680.80`.
- Chrome comparison engine reported by CDP: Chrome `146.0.7680.80`, headless. Both runs therefore used the same browser version; the comparison primarily measures the agent interface/workflow, not rendering-engine differences.

## Search result

Both isolated runs loaded the same price-ascending AliExpress query and returned 12 unique visible cards (the page exposed fewer than the 24-card budget). The first six product IDs matched exactly; 10 of 12 IDs overlapped. The two later-card differences are consistent with live marketplace volatility between sequential runs.

### Cheapest verified candidates

1. `1005009530171751` — detail page describes a steel open case sized for standard ATX (`17.3 × 10.4 × 5 in`). Displayed item price `₩9,416`; no separate shipping-charge amount was displayed, only estimated delivery `Oct 18–24`. Rating `4.5`, 22 reviews, 162 sold.
   - https://www.aliexpress.com/item/1005009530171751.html
2. `1005009032804883` — ATX/Micro-ATX/ITX open test-bench case. New-member displayed price `₩10,645` (regular/reference `₩16,153`); no separate shipping-charge amount was displayed.
   - https://www.aliexpress.com/item/1005009032804883.html
3. `1005010759560374` — ATX/MATX/ITX open-air case. Detail-page price `₩15,300`; no separate shipping-charge amount was displayed. Search-card price was `₩6,500`, so the detail page was treated as authoritative.
   - https://www.aliexpress.com/item/1005010759560374.html
4. `1005012714295786` — ATX/Micro-ATX stackable test-bench frame. New-member item `₩4,802` + shipping `₩10,758` = displayed delivered total `₩15,560` (regular/reference item price `₩17,184`).
   - https://www.aliexpress.com/item/1005012714295786.html
5. `1005012333027894` — ATX/MATX open-frame test bench. New-member item `₩6,480` + shipping `₩9,483` = displayed delivered total `₩15,963` (regular/reference item price `₩12,233`).
   - https://www.aliexpress.com/item/1005012333027894.html

Excluded `1005013035544682` from the recommendation because the selected low-price variant was labelled `Chassis casters`, indicating an accessory rather than the complete frame.

**Best current pick:** `1005009530171751` at `₩9,416` displayed, because its detail page explicitly confirms standard ATX dimensions and it had no separate shipping-charge amount on the page. AliExpress prices, member discounts, variants, and shipping can change at checkout.

## Benchmark findings

### Quality

- Search collection: tie on count (12/12 unique cards), same first six IDs, 10/12 total ID overlap.
- Chrome verified all six cheapest product pages and exposed prices, variants, shipping text, and compatibility evidence.
- AriBrowser verified the first three product pages. The next three returned only an empty-title AliExpress shell. Three fresh-profile retries with longer waits produced the same shell. Therefore, full end-to-end quality was lower in this run and the full-run efficiency comparison does not pass the quality gate.

### Speed

- Equivalent search/collection phase: AriBrowser `8.574 s`; Chrome `8.491 s`. Chrome was `0.97%` faster—effectively a tie.
- End-to-end attempt including six detail checks and AriBrowser retries: AriBrowser `69.298 s`; Chrome `36.403 s`. AriBrowser took `90.36%` longer, but this number includes failed retries and should be read as observed workflow cost, not a clean engine benchmark.

### Token/observation efficiency

Exact provider API token usage could not be isolated inside this shared agent session. Counts below are `o200k_base` proxy tokens over saved agent-visible observation payloads, not billed API usage.

- Equivalent search phase: AriBrowser `13,619` proxy tokens; Chrome `5,942`.
- Chrome used `56.37%` fewer proxy observation tokens; AriBrowser/Chrome ratio `2.292×`.
- Full observed workflow including failed AriBrowser detail responses and retries: AriBrowser `129,880`; Chrome `15,923` (`8.157×`). This diagnostic total is not a valid efficiency winner because the quality/coverage differed.
- The search-token result measures interface strategy as well as browser: AriBrowser returned native navigation summaries/events plus `page.state`/`extract`, while Chrome used browser-side targeted DOM extraction. It is not an engine-only comparison.

### Convenience

- AriBrowser strengths: native JSONL, structured prices/URLs, automatic repeated-card detection, stable short IDs, explicit page-state/sort detection, built-in byte/token budgets, doctor/self-test, and strong sandbox diagnostics.
- AriBrowser friction observed: a fresh installation path requires root AppArmor policy installation for bundled Chromium; system Chrome fallback was needed. Navigation summaries included noisy network-event URLs. Product-detail rendering failed silently as a successful navigation with an empty title/shell, requiring manual quality checks and retries.
- Chrome strengths: no installation work, all six product pages rendered correctly, and targeted DOM extraction was compact and fast.
- Chrome friction: the agent had to write and maintain site-specific JavaScript selectors/normalization; malformed concatenated text around prices needed cleanup. It lacks AriBrowser's native page-state and extraction schema unless custom code is added.

## Verdict

For this AliExpress shopping task, **Chrome was the practical winner**: equal search speed, lower agent-visible payload, and complete product-detail verification. AriBrowser's structured search extraction was pleasant and accurate for the first six results, but its product-detail failure and verbose navigation observations erased the expected efficiency benefit.

## Audit artifacts

- `benchmark-contract.md`
- `doctor-system-chrome.txt`, `self-test-system-chrome.txt`
- `aribrowser-raw.json`, `chrome-raw.json`
- `aribrowser-verification-raw.json`, `aribrowser-verification-retries-raw.json`, `chrome-verification-raw.json`
- `aribrowser-results.json`, `chrome-results.json`, `search-comparison.json`
- `token-metrics.json`, `derived-metrics.json`