/**
 * 탐색 직후 페이지가 "실제 내용"을 담고 있는지 판단할 신호를 모은다.
 *
 * SPA 상세 페이지는 HTML 골격만 먼저 내려오고 데이터는 뒤늦게 채워지며, 자동화 차단
 * 사이트는 HTTP 200으로 빈 껍데기나 보안 문자(captcha) 프레임만 돌려준다. 어느 경우든
 * `load` 이벤트는 정상적으로 발생하므로 이벤트만으로는 "성공"을 판정할 수 없다.
 * 이 스크립트는 판정 자체를 하지 않고 Python(readiness.py)이 분류에 쓸 사실만 돌려준다.
 *
 * 비용을 억제하기 위해 본문 텍스트는 상한(60,000자)까지만 조립하고, 요소 통계는
 * 간단한 선택자 질의로만 센다. 페이지 데이터를 변경하지 않는다.
 */
(params) => {
  const lib = (/*TEXTLIB*/);
  const textLimit = params.textLimit || 60000;
  const body = document.body;
  const visible = element => element instanceof Element && !lib.hidden(element) && element.getClientRects().length > 0;
  const text = body ? lib.textOf(body, textLimit) : '';
  const main = document.querySelector('main, [role=main], article, #content, #main, #container');
  const mainText = main && main !== body ? lib.textOf(main, textLimit) : null;
  let chromeParts = [];
  // 내용 텍스트: main이 있으면 그 텍스트, 없으면 본문에서 머리글/탐색/바닥글 텍스트를 뺀 길이.
  // 빈 껍데기 페이지도 공통 머리글·바닥글 링크만으로 수백 자가 나오므로 그 부분을 제외해야 한다.
  // 시맨틱 태그가 없는 사이트(div.footer 등)도 많으므로 body에서 깊이 4까지의 머리글/탐색/바닥글류 클래스·ID도 포함한다.
  let chrome = 0;
  if (mainText == null) {
    const CHROME = /(^|[\s_-])(header|footer|nav|navbar|navigation|gnb|lnb|menu|breadcrumb|cookie|banner|topbar|sidebar)([\s_-]|$)/i;
    const parts = [];
    for (const part of document.querySelectorAll('header, nav, footer, [role=banner], [role=navigation], [role=contentinfo]')) parts.push(part);
    const walk = (element, depth) => {
      if (depth > 4) return;
      for (const child of element.children) {
        if (lib.SKIP.has(child.tagName)) continue;
        if (CHROME.test(child.className + ' ' + child.id)) parts.push(child); else walk(child, depth + 1);
      }
    };
    if (body) walk(body, 1);
    const counted = parts.filter(part => !part.closest('main, [role=main]') && !parts.some(other => other !== part && other.contains(part)));
    for (const part of counted) {
      chrome += lib.textOf(part, 5000).length;
      if (chrome > textLimit) break;
    }
    chromeParts = counted;
  }
  const contentChars = mainText != null ? mainText.length : Math.max(0, text.length - chrome);
  // 내용 영역의 상호작용 요소 수: 머리글/바닥글 링크만 많은 껍데기를 "내용 있음"으로 오인하지 않기 위한 값.
  const contentRoot = main && main !== body ? main : body;
  const inChrome = element => chromeParts.some(part => part.contains(element));
  const contentCount = selector => {
    let found = 0;
    try {
      for (const element of (contentRoot || document).querySelectorAll(selector)) {
        if (visible(element) && !inChrome(element)) found++;
        if (found >= 500) break;
      }
    } catch { /* 선택자는 고정이다. */ }
    return found;
  };
  const count = selector => { try { return document.querySelectorAll(selector).length; } catch { return 0; } };
  const visibleCount = (selector, limit = 2000) => {
    let found = 0;
    try {
      for (const element of document.querySelectorAll(selector)) { if (visible(element)) found++; if (found >= limit) break; }
    } catch { /* 잘못된 선택자는 없다. */ }
    return found;
  };

  // ---------- 보안 문자 / 차단 근거 ----------
  const CAPTCHA = /captcha|recaptcha|hcaptcha|geetest|_____tmd_____|\/punish|nocaptcha|slidecaptcha|turnstile|challenge-platform|cf-chl|perimeterx|px-captcha|datadome|distil|imperva|incapsula|bot-?detect|verify-?human|baxia/i;
  const evidence = [];
  for (const frame of document.querySelectorAll('iframe[src]')) {
    if (CAPTCHA.test(frame.src)) evidence.push({kind: 'iframe', value: frame.src.slice(0, 120), visible: visible(frame)});
    if (evidence.length >= 5) break;
  }
  for (const script of document.querySelectorAll('script[src]')) {
    if (/captcha|geetest|turnstile|challenge-platform|datadome|perimeterx|baxia/i.test(script.src)) evidence.push({kind: 'script', value: script.src.slice(0, 120)});
    if (evidence.length >= 8) break;
  }
  for (const element of document.querySelectorAll('[id*="captcha" i], [class*="captcha" i], [id*="nocaptcha" i], [class*="baxia" i], [id*="baxia" i], [class*="slide-verify" i], [class*="verify" i][class*="slide" i], [class*="challenge" i]')) {
    if (visible(element)) evidence.push({kind: 'element', value: (element.id || element.className || '').toString().slice(0, 80)});
    if (evidence.length >= 10) break;
  }

  // ---------- 로딩 중 표시 ----------
  const skeletons = visibleCount('[class*="skeleton" i], [class*="placeholder" i]:not(input):not(textarea), [class*="shimmer" i], [aria-busy="true"]', 200);
  const spinners = visibleCount('[class*="spinner" i], [class*="loading" i], [class*="loader" i], [role="progressbar"]', 200);
  // SPA 루트가 비어 있으면(자식 0~1개, 텍스트 거의 없음) 아직 렌더되지 않은 것이다.
  const roots = ['#root', '#app', '#__next', '#__nuxt', '#__layout', '[data-reactroot]', '#main'];
  let appRoot = null;
  for (const selector of roots) {
    const element = document.querySelector(selector);
    if (element) { appRoot = {selector, children: element.children.length, text_chars: lib.textOf(element, 2000).length}; break; }
  }

  // ---------- 반복 목록/가격/상품 신호(페이지 종류 추정용) ----------
  let repeatedGroups = 0;
  let visited = 0;
  const queue = body ? [body] : [];
  while (queue.length && visited < 1500) {
    const element = queue.shift();
    visited++;
    const groups = new Map();
    for (const child of element.children) {
      if (lib.SKIP.has(child.tagName)) continue;
      const key = child.tagName + '.' + [...child.classList].slice(0, 2).join('.');
      groups.set(key, (groups.get(key) || 0) + 1);
      if (child.children.length) queue.push(child);
    }
    for (const size of groups.values()) if (size >= 4) repeatedGroups++;
  }
  const prices = lib.findPrices(text.slice(0, 30000), 50).length;
  const structured = {
    json_ld_product: [...document.querySelectorAll('script[type="application/ld+json"]')].some(s => /"@type"\s*:\s*"?(Product|ItemList|Offer)/i.test(s.textContent || '')),
    og_type: document.querySelector('meta[property="og:type"]')?.getAttribute('content') || null,
    og_title: !!document.querySelector('meta[property="og:title"]'),
  };

  return {
    url: location.href, title: document.title || '', ready_state: document.readyState,
    text_chars: text.length, main_text_chars: mainText == null ? null : mainText.length, content_text_chars: contentChars,
    sample: text.slice(0, 160), nodes: document.getElementsByTagName('*').length,
    links: visibleCount('a[href]'), images: visibleCount('img'), headings: visibleCount('h1, h2, h3'),
    content_links: contentCount('a[href]'), content_buttons: contentCount('button, [role=button], input[type=submit]'),
    content_inputs: contentCount('input:not([type=hidden]), textarea, select'),
    h1: lib.textOf(document.querySelector('h1'), 200) || null,
    forms: count('form'), inputs: visibleCount('input:not([type=hidden]), textarea, select'),
    password_inputs: visibleCount('input[type=password]'),
    buttons: visibleCount('button, [role=button], input[type=submit]'),
    skeletons, spinners, app_root: appRoot, captcha: evidence.slice(0, 10),
    repeated_groups: repeatedGroups, prices, structured,
    error_page: location.href.startsWith('chrome-error://'),
    noscript: !!document.querySelector('noscript') && text.length < 300,
  };
}
