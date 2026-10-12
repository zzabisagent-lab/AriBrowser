/**
 * 검색 결과·목록 페이지의 정렬 조건, 활성 필터, 페이지네이션, 전체 결과 수,
 * 페이지당 표시 수, 검색어, 체크 토글을 구조화해 반환한다.
 *
 * 사이트마다 마크업이 다르므로 URL 매개변수, 네이티브 select, aria 상태,
 * 활성 클래스 이름, 한국어/영어 키워드를 함께 사용하는 휴리스틱이다. 각 항목에
 * `source`(url/select/active_control/text)와 후보 목록을 함께 돌려 Agent가
 * 신뢰도를 판단할 수 있게 한다. 페이지 데이터를 읽기만 하며 변경하지 않는다.
 * 반환하는 요소 ID는 긴 내부 ID이며 Python 쪽에서 짧은 ID로 변환된다.
 */
(params) => {
  const registry = (/*REGISTRY*/)(params.prefix);
  const normalize = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const clip = (value, limit = 120) => normalize(value).slice(0, limit);
  const isVisible = element => element.checkVisibility
    ? element.checkVisibility({visibilityProperty: true}) : element.getClientRects().length > 0;
  const idOf = element => registry.identify(element);
  const url = new URL(location.href);

  // ---------- 키워드 표 (한국어/영어) ----------
  const SORT_PARAM = /^(sort|sorttype|sort_type|sortby|sort_by|sortkey|order|orderby|order_by|ordering|align|srt|sortmethod)$/i;
  const PAGE_PARAM = /^(page|p|pageno|page_no|pagenum|page_num|pg|pageindex|page_index|curpage|cur_page|pagenumber)$/i;
  const PER_PAGE_PARAM = /^(limit|size|pagesize|page_size|per_page|perpage|num|count|rows|pcount|listsize|list_size|viewsize|view_size|display|psize|n)$/i;
  const QUERY_PARAM = /^(q|query|keyword|keywords|k|search|searchword|search_word|word|w|term|text|s)$/i;
  const SORT_TEXT = /정렬|가격순|낮은\s?가격|높은\s?가격|인기순|최신순|신상품|리뷰|판매량|추천순|평점|등록일|이름순|\bsort\b|\border by\b|\bprice\b|\bpopular|\bnewest|\brelevan|\brating|\bbest\s?sell|\btop rated|\blowest|\bhighest/i;
  const SORT_VALUE = /낮은|높은|가격|인기|최신|신상|리뷰|판매|추천|평점|등록|이름|price|popular|new|relevan|rating|best|low|high|date|name|asc|desc/i;
  const NEXT_TEXT = /^(다음|다음\s?페이지|next|next page|›|>|»|→)$/i;
  const PREV_TEXT = /^(이전|이전\s?페이지|prev|previous|‹|<|«|←)$/i;
  const ACTIVE_CLASS = /(^|[\s_-])(on|active|selected|current|sel|checked|is-active|is-selected|is-current)([\s_-]|$)/i;
  const TOGGLE_KEYWORD = /배송비|무료배송|포함|제외|할인|쿠폰|재고|품절|당일|직구|해외|ship|deliver|include|exclude|free|stock|sale|discount/i;

  // ---------- URL 매개변수 ----------
  const urlParams = {};
  for (const [key, value] of url.searchParams) {
    if (key in urlParams) urlParams[key] = [].concat(urlParams[key], value);
    else urlParams[key] = value;
  }
  const paramMatching = pattern => Object.keys(urlParams).find(key => pattern.test(key));

  // ---------- 정렬 ----------
  const sortCandidates = [];
  const sortParam = paramMatching(SORT_PARAM);
  if (sortParam) sortCandidates.push({value: String(urlParams[sortParam]), label: null, source: 'url', param: sortParam});
  for (const selectElement of document.querySelectorAll('select')) {
    if (!isVisible(selectElement)) continue;
    const options = [...selectElement.options];
    const labelText = normalize((selectElement.labels && [...selectElement.labels].map(l => l.textContent).join(' ')) ||
      selectElement.getAttribute('aria-label') || selectElement.name || selectElement.id);
    const optionText = options.map(o => o.text).join(' ');
    if (SORT_TEXT.test(labelText) || (options.length >= 2 && options.filter(o => SORT_VALUE.test(o.text)).length >= 2)) {
      const selected = options[selectElement.selectedIndex];
      sortCandidates.push({value: selected ? selected.value : null, label: selected ? normalize(selected.text) : null,
        source: 'select', id: idOf(selectElement), options: options.slice(0, 20).map(o => ({value: o.value, label: normalize(o.text)}))});
    } else if (SORT_TEXT.test(optionText) && options.length >= 2) {
      const selected = options[selectElement.selectedIndex];
      sortCandidates.push({value: selected?.value ?? null, label: selected ? normalize(selected.text) : null, source: 'select', id: idOf(selectElement)});
    }
  }
  // 정렬 링크/버튼 묶음: 같은 부모 아래 정렬 키워드를 가진 조작 요소 2개 이상에서 활성 항목을 찾는다.
  const sortGroups = new Map();
  for (const control of document.querySelectorAll('a, button, [role=button], [role=tab], [role=option], [role=radio], label, li, span')) {
    const text = normalize(control.innerText || control.textContent);
    if (!text || text.length > 40 || !SORT_VALUE.test(text) || !isVisible(control)) continue;
    const parent = control.parentElement?.closest('ul, ol, div, nav, form, fieldset, [role=group], [role=tablist], [role=radiogroup]');
    if (!parent) continue;
    if (!sortGroups.has(parent)) sortGroups.set(parent, []);
    sortGroups.get(parent).push(control);
  }
  for (const [parent, controls] of sortGroups) {
    const distinct = new Set(controls.map(c => normalize(c.innerText || c.textContent)));
    if (distinct.size < 2) continue;
    const groupText = normalize(parent.innerText || parent.textContent);
    if (!SORT_TEXT.test(groupText) && distinct.size < 3) continue;
    const active = controls.find(c => c.getAttribute('aria-current') || c.getAttribute('aria-selected') === 'true' ||
      c.getAttribute('aria-pressed') === 'true' || c.getAttribute('aria-checked') === 'true' || ACTIVE_CLASS.test(c.className) ||
      (c.parentElement && ACTIVE_CLASS.test(c.parentElement.className)) ||
      (c.querySelector && c.querySelector('input[type=radio]:checked')) || getComputedStyle(c).fontWeight >= 600);
    sortCandidates.push({value: active ? normalize(active.innerText || active.textContent) : null,
      label: active ? normalize(active.innerText || active.textContent) : null, source: 'active_control',
      id: active ? idOf(active) : null, group: idOf(parent),
      options: [...distinct].slice(0, 20).map(label => ({label, id: idOf(controls.find(c => normalize(c.innerText || c.textContent) === label))}))});
  }
  const sort = sortCandidates.find(c => c.value != null) || sortCandidates[0] || null;
  // 채택한 후보를 맨 앞에 두어 Agent가 근거를 바로 볼 수 있게 한다.
  if (sort) sortCandidates.sort((a, b) => (b === sort) - (a === sort));

  // ---------- 페이지네이션 ----------
  const pagination = {current_page: null, total_pages: null, has_next: null, has_prev: null, next: null, prev: null, source: null, pages: []};
  const pageParam = paramMatching(PAGE_PARAM);
  if (pageParam && /^\d+$/.test(String(urlParams[pageParam]))) {
    pagination.current_page = Number(urlParams[pageParam]);
    pagination.source = 'url';
    pagination.param = pageParam;
  }
  const navCandidates = [...document.querySelectorAll('nav, [role=navigation], ul, ol, div')].filter(node =>
    isVisible(node) && (/pag(e|ing|ination)|pager|페이지/i.test(node.className + ' ' + (node.getAttribute('aria-label') || '') + ' ' + node.id) ||
      [...node.querySelectorAll(':scope > li > a, :scope > a, :scope > li > button, :scope > button, :scope > span, :scope > strong, :scope > em, :scope > li > strong, :scope > li > span')]
        .filter(n => /^\d{1,4}$/.test(normalize(n.textContent))).length >= 3));
  const pageNav = navCandidates.sort((a, b) => b.querySelectorAll('a, button').length - a.querySelectorAll('a, button').length)[0] || null;
  if (pageNav) {
    pagination.source = pagination.source ? 'url+navigation' : 'navigation';
    pagination.element = idOf(pageNav);
    const numeric = [...pageNav.querySelectorAll('a, button, span, strong, em, b, li')].filter(n => /^\d{1,4}$/.test(normalize(n.textContent)) &&
      !/^\d{1,4}$/.test(normalize(n.parentElement?.textContent)) || (/^\d{1,4}$/.test(normalize(n.textContent)) && n.children.length === 0));
    const seen = new Set();
    for (const node of numeric) {
      const number = Number(normalize(node.textContent));
      if (seen.has(number)) continue;
      seen.add(number);
      const active = node.getAttribute('aria-current') || ACTIVE_CLASS.test(node.className) || ACTIVE_CLASS.test(node.parentElement?.className || '') ||
        (!(node instanceof HTMLAnchorElement) && !(node instanceof HTMLButtonElement) && !node.closest('a, button'));
      pagination.pages.push({page: number, id: idOf(node.closest('a, button') || node), active: !!active});
    }
    const activePage = pagination.pages.find(p => p.active);
    if (activePage && pagination.current_page == null) pagination.current_page = activePage.page;
    if (pagination.pages.length) pagination.total_pages = Math.max(...pagination.pages.map(p => p.page));
    const links = [...pageNav.querySelectorAll('a, button, [role=button]')];
    const next = links.find(l => l.rel === 'next' || NEXT_TEXT.test(normalize(l.innerText || l.textContent)) || /next|다음/i.test(l.getAttribute('aria-label') || l.className || l.title || ''));
    const prev = links.find(l => l.rel === 'prev' || PREV_TEXT.test(normalize(l.innerText || l.textContent)) || /prev|이전/i.test(l.getAttribute('aria-label') || l.className || l.title || ''));
    const disabled = node => !node || node.disabled || node.getAttribute('aria-disabled') === 'true' || /disabled/i.test(node.className) || (node instanceof HTMLAnchorElement && !node.getAttribute('href'));
    if (next) { pagination.next = idOf(next); pagination.has_next = !disabled(next); }
    if (prev) { pagination.prev = idOf(prev); pagination.has_prev = !disabled(prev); }
    if (pagination.has_next == null && pagination.current_page != null && pagination.total_pages != null) {
      pagination.has_next = pagination.current_page < pagination.total_pages;
      const nextPage = pagination.pages.find(p => p.page === pagination.current_page + 1);
      if (nextPage) pagination.next = nextPage.id;
    }
    if (pagination.has_prev == null && pagination.current_page != null) pagination.has_prev = pagination.current_page > 1;
  } else {
    const relNext = document.querySelector('a[rel=next], link[rel=next]');
    if (relNext) { pagination.has_next = true; pagination.next = relNext instanceof HTMLAnchorElement ? idOf(relNext) : null; pagination.source = pagination.source || 'rel'; }
  }
  if (pagination.current_page == null && !pageNav) pagination.current_page = 1;
  pagination.pages = pagination.pages.slice(0, 30);

  // ---------- 페이지당 표시 수 ----------
  let perPage = null;
  const perPageParam = paramMatching(PER_PAGE_PARAM);
  if (perPageParam && /^\d+$/.test(String(urlParams[perPageParam]))) perPage = {value: Number(urlParams[perPageParam]), source: 'url', param: perPageParam};
  if (!perPage) {
    for (const selectElement of document.querySelectorAll('select')) {
      const options = [...selectElement.options];
      if (options.length >= 2 && options.every(o => /^\d{1,4}\s*(개|건|items?|per page|rows)?(씩|씩\s?보기|\s?보기)?$/i.test(normalize(o.text)))) {
        const selected = options[selectElement.selectedIndex];
        perPage = {value: selected ? Number(normalize(selected.text).match(/\d+/)[0]) : null, source: 'select', id: idOf(selectElement),
          options: options.map(o => Number(normalize(o.text).match(/\d+/)[0]))};
        break;
      }
    }
  }

  // ---------- 전체 결과 수 ----------
  const bodyText = normalize(document.body ? document.body.innerText.slice(0, 200000) : '');
  const TOTAL_PATTERNS = [
    /(?:총|전체|검색\s?결과|결과)\s*:?\s*([\d,]{1,12})\s*(?:개|건|개의|건의|items?|results?|products?)/i,
    /(?:전체|all|total)\s*\(\s*([\d,]{1,12})\s*\)/i,
    /([\d,]{1,12})\s*(?:개|건)\s*(?:의\s*)?(?:상품|결과|검색\s?결과|게시물|게시글|아이템|리뷰)/,
    /(?:of|total|all)\s+([\d,]{1,12})\s+(?:results?|items?|products?|entries|records)/i,
    /([\d,]{1,12})\s+(?:results?|items?|products?)\s+(?:found|total)?/i,
  ];
  let totalResults = null;
  for (const pattern of TOTAL_PATTERNS) {
    const match = bodyText.match(pattern);
    if (match) { totalResults = {value: Number(match[1].replace(/,/g, '')), text: clip(match[0], 80), source: 'text'}; break; }
  }

  // ---------- 검색어 ----------
  let query = null;
  const queryParam = paramMatching(QUERY_PARAM);
  if (queryParam) query = {value: String(urlParams[queryParam]), source: 'url', param: queryParam};
  else {
    const input = document.querySelector('input[type=search], input[name*="query" i], input[name*="keyword" i], input[name="q"], input[role=searchbox], [role=searchbox]');
    if (input && input.value) query = {value: input.value, source: 'input', id: idOf(input)};
  }

  // ---------- 활성 필터와 토글 ----------
  const filters = [];
  const toggles = [];
  const labelFor = control => normalize((control.labels && [...control.labels].map(l => l.textContent).join(' ')) ||
    control.getAttribute('aria-label') || control.closest('label')?.textContent || control.parentElement?.textContent || control.value || control.name);
  for (const control of document.querySelectorAll('input[type=checkbox], input[type=radio], [role=checkbox], [role=switch], [role=radio]')) {
    if (!isVisible(control) && !control.closest('label')) continue;
    const checked = control.checked ?? (control.getAttribute('aria-checked') === 'true');
    const label = clip(labelFor(control), 80);
    if (!label) continue;
    const group = clip(control.closest('fieldset')?.querySelector('legend')?.textContent ||
      control.closest('[role=group], [role=radiogroup]')?.getAttribute('aria-label') || '', 60) || null;
    const entry = {label, checked: !!checked, id: idOf(control), group};
    if (TOGGLE_KEYWORD.test(label) || control.matches('[role=switch]')) toggles.push(entry);
    if (checked && (control.type !== 'radio' || label !== group)) filters.push({label, source: 'checked', id: entry.id, group});
  }
  for (const chip of document.querySelectorAll('[aria-pressed=true], [aria-selected=true]:not(option):not([role=tab]), [aria-checked=true]:not(input)')) {
    if (!isVisible(chip) || chip.matches('input, [role=checkbox], [role=switch], [role=radio]')) continue;
    const label = clip(chip.innerText || chip.textContent, 80);
    if (label && !/^\d{1,4}$/.test(label) && !SORT_VALUE.test(label)) filters.push({label, source: 'aria', id: idOf(chip)});
  }
  for (const [key, value] of Object.entries(urlParams)) {
    if (SORT_PARAM.test(key) || PAGE_PARAM.test(key) || PER_PAGE_PARAM.test(key) || QUERY_PARAM.test(key)) continue;
    if (value === '' || value == null) continue;
    filters.push({label: `${key}=${Array.isArray(value) ? value.join(',') : value}`, source: 'url', param: key});
  }

  return {
    url: location.href, url_params: urlParams, query, sort: sort ? {...sort, candidates: sortCandidates.length} : null,
    sort_candidates: sortCandidates.slice(0, 5), pagination, per_page: perPage, total_results: totalResults,
    filters: filters.slice(0, 50), toggles: toggles.slice(0, 50),
  };
}
