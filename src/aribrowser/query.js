/**
 * 브라우저 안에서 CSS 선택자 질의(query)와 반복 레코드 추출(extract)을 수행한다.
 *
 * Agent가 전체 DOM을 받아 밖에서 걸러내는 대신, 필요한 요소와 필드만 Chromium
 * 내부에서 선별해 작은 결과만 돌려주기 위한 고정 코드다. 임의 JavaScript를
 * 실행하지 않으며 입력은 CSS 선택자·필드 이름·속성 이름 같은 데이터뿐이다.
 * 쿠키·저장소·네트워크 API에는 접근하지 않는다.
 *
 * mode = 'query'  : selector에 맞는 요소를 찾아 요청한 필드만 반환한다.
 * mode = 'records': 상품 카드·게시글처럼 같은 구조가 반복되는 항목을 레코드 단위로
 *                   묶어 반환한다. item 선택자가 없으면 반복 구조를 자동 감지한다.
 *
 * 반환하는 요소 ID는 긴 내부 ID이며 Python 쪽에서 짧은 ID로 변환된다.
 *
 * v0.2.0: 텍스트 조립과 가격·판매량·평점·리뷰·할인 해석은 textlib.js(TEXTLIB 자리에 삽입)를
 * 공유한다. 요소 텍스트는 innerText 대신 textlib.textOf로 읽어 인라인 요소가 붙어 출력되는
 * "₩12,3451,000+ 판매" 같은 문제를 피하고, 자동 필드에 sold/rating/reviews/original_price/
 * discount를 추가했다. 레코드 응답에는 필드 커버리지 기반 품질 지표(quality)를 함께 돌려준다.
 *
 * v0.3.0(Amazon 벤치마크 후속):
 * - 중복 제거 키가 전부 null/빈 문자열인 레코드는 중복 제거 대상에서 제외해 보존한다(dedupe.skipped_records).
 *   0.2.0은 null 키를 "[null]"로 직렬화해 URL 선택자가 틀리면 48개 카드가 1개로 줄어드는 문제가 있었다.
 * - quality에 원본/출력 레코드 수, 제거 비율, 키 커버리지, 경고를 더해 결과 손실과 실제 중복을 구분한다.
 * - 지정 필드: source="self"(레코드 자체), relation="closest"(가장 가까운 조상 링크), type="url"의 href 자동 탐색
 *   (자기 href → 가장 가까운 a[href] → 내부 첫 a[href], 출처는 record.sources), canonical=true(추적 매개변수 제거와
 *   안정 식별자), explain=true(선택자 일치 수·원문·해석 상태), 세분화한 missing 사유.
 * - 자동 필드에 recent_sales(기간 한정 구매량)를 추가했다. 누적 sold와 같은 수치로 취급하지 않는다.
 */
(params) => {
  const registry = (/*REGISTRY*/)(params.prefix);
  const lib = (/*TEXTLIB*/);
  const maxText = params.maxText || 500;
  const skipTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'META', 'LINK', 'HEAD']);
  const normalize = lib.normalize;
  /** 요소의 표시 텍스트(경계 규칙 적용)를 길이 제한과 함께 읽는다. */
  const textOf = (element, limit = maxText) => lib.textOf(element, Math.max(limit, 1));
  const clip = (value, limit = maxText) => {
    const text = normalize(value);
    return text.length > limit ? text.slice(0, limit) : text;
  };
  /** display:none·visibility:hidden·크기 0 요소를 숨김으로 본다. */
  const isVisible = element => {
    if (!(element instanceof Element)) return false;
    if (element.checkVisibility) return element.checkVisibility({visibilityProperty: true, contentVisibilityAuto: true});
    const css = getComputedStyle(element);
    return css.display !== 'none' && css.visibility !== 'hidden' && element.getClientRects().length > 0;
  };
  /** 잘못된 선택자는 예외 대신 오류 객체로 보고해 Python이 INVALID_ARGUMENT로 바꾼다. */
  const select = (root, selector, all) => {
    try {
      return all ? [...root.querySelectorAll(selector)] : root.querySelector(selector);
    } catch (error) {
      throw {code: 'INVALID_SELECTOR', selector, message: String(error.message || error)};
    }
  };
  const parsePrice = lib.parsePrice;
  const parseNumber = lib.parseNumber;
  /** 접근 가능한 이름의 경량 근사. 전체 AX 계산은 collector/Chromium AX가 맡는다. */
  const nameOf = element => normalize(element.getAttribute('aria-label') || element.getAttribute('alt') ||
    element.getAttribute('title') || element.getAttribute('placeholder') || '');
  const roleOf = element => element.getAttribute('role') || ({A: 'link', BUTTON: 'button', IMG: 'image', INPUT: 'textbox',
    SELECT: 'combobox', TEXTAREA: 'textbox', H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading', LI: 'listitem',
    UL: 'list', OL: 'list', TABLE: 'table', TR: 'row', TD: 'cell', TH: 'columnheader', FORM: 'form', NAV: 'navigation'})[element.tagName] || null;
  const absoluteUrl = value => {
    if (!value) return null;
    try { return new URL(value, document.baseURI).href; } catch { return null; }
  };
  const attributeOf = (element, name) => {
    if (name === 'href' && element instanceof HTMLAnchorElement) return element.href || null;
    if (name === 'src') return element.currentSrc || element.src || element.getAttribute('src') || element.getAttribute('data-src') || null;
    if (name === 'value' && 'value' in element) return element.type === 'password' ? '[REDACTED]' : element.value;
    const value = element.getAttribute(name);
    return value == null ? null : value;
  };

  /** dedupe 키 값이 "있는" 값인지: null/undefined/빈 문자열/빈 배열은 비교 근거가 될 수 없다. */
  const usable = value => value != null && value !== '' && !(Array.isArray(value) && !value.length);
  /** canonical URL 객체({raw, canonical, stable_id})는 안정 식별자(없으면 canonical)로 비교해 추적 매개변수만 다른 URL을 같은 것으로 본다. */
  const keyValue = value => (value && typeof value === 'object' && !Array.isArray(value) && 'canonical' in value)
    ? (value.stable_id ?? value.canonical) : value;
  /** 중복 제거 통계 초기화. 키별 유효값 수를 세어 커버리지를 계산한다. */
  const startDedupe = keys => ({keys, usable_records: 0, skipped_records: 0, valid: Object.fromEntries(keys.map(k => [k, 0]))});
  /**
   * 중복 제거 통계를 응답 형태로 마무리한다. key_coverage는 원본 레코드 대비 키별 유효값 비율이며,
   * 모든 레코드의 키가 비어 있었으면 경고를 남긴다(0.2.0에서 레코드가 1개로 줄던 상황).
   */
  const finishDedupe = (stats, sourceCount, duplicates) => {
    const coverage = {};
    for (const key of stats.keys) coverage[key] = sourceCount ? Math.round(stats.valid[key] / sourceCount * 100) / 100 : 0;
    const result = {keys: stats.keys, usable_records: stats.usable_records, skipped_records: stats.skipped_records,
      duplicates_removed: duplicates, key_coverage: coverage};
    if (sourceCount && stats.usable_records === 0) result.warning = 'dedupe keys were empty; records were preserved';
    else if (stats.skipped_records) result.warning = `${stats.skipped_records} records had empty dedupe keys and were preserved`;
    return result;
  };
  /** 키별 유효값 수를 누적한다(query/records 공용). */
  const countKeys = (stats, values) => {
    stats.keys.forEach((key, index) => { if (usable(values[index])) stats.valid[key]++; });
  };

  /** 선택자/정규식 오류는 예외 대신 {error} 객체로 돌려 Python이 INVALID_ARGUMENT로 변환한다. */
  function run() {
    // ---------- 범위(scope) 해석 ----------
    let root = document.body || document.documentElement;
    if (params.scope) {
      root = registry.get(params.scope);
      if (!root) return {error: 'SCOPE_NOT_FOUND', scope: params.scope};
    } else if (params.scopeSelector) {
      root = select(document, params.scopeSelector, false);
      if (!root) return {error: 'SCOPE_NOT_FOUND', scope: params.scopeSelector};
    }

    // ---------- 텍스트 조건 ----------
    let textTest = null;
    if (params.text) {
      const needle = String(params.text);
      if (params.match === 'regex') {
        let pattern;
        try { pattern = new RegExp(needle, 'i'); } catch (error) { return {error: 'INVALID_REGEX', message: String(error.message)}; }
        textTest = value => pattern.test(value);
      } else if (params.match === 'exact') {
        textTest = value => value.toLowerCase() === needle.toLowerCase();
      } else if (params.match === 'prefix') {
        textTest = value => value.toLowerCase().startsWith(needle.toLowerCase());
      } else {
        textTest = value => value.toLowerCase().includes(needle.toLowerCase());
      }
    }

    // ---------- query 모드 ----------
    if (params.mode === 'query') {
      let elements = select(root, params.selector, true);
      if (!params.includeHidden) elements = elements.filter(isVisible);
      if (textTest) elements = elements.filter(el => textTest(textOf(el, 20000)) || textTest(nameOf(el)));
      const total = elements.length;
      const fields = params.fields || ['id', 'tag', 'text', 'href'];
      const items = [];
      const seen = new Set();
      let duplicates = 0;
      const dedupeKeys = params.dedupe ? (Array.isArray(params.dedupe) ? params.dedupe : ['text', 'href']) : null;
      const dedupeStats = dedupeKeys ? startDedupe(dedupeKeys) : null;
      for (const element of elements) {
        const item = {};
        for (const field of fields) {
          if (field === 'id') item.id = registry.identify(element);
          else if (field === 'tag') item.tag = element.tagName.toLowerCase();
          else if (field === 'text') item.text = textOf(element);
          else if (field === 'name') item.name = nameOf(element);
          else if (field === 'role') item.role = roleOf(element);
          else if (field === 'href') item.href = element instanceof HTMLAnchorElement ? element.href : (absoluteUrl(element.getAttribute('href')) || null);
          else if (field === 'src') item.src = attributeOf(element, 'src');
          else if (field === 'value') item.value = attributeOf(element, 'value');
          else if (field === 'visible') item.visible = isVisible(element);
          else if (field === 'classes') item.classes = [...element.classList].slice(0, 20);
          else if (field === 'bounds') {
            const rect = element.getBoundingClientRect();
            item.bounds = {x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height)};
          } else if (field === 'price') item.price = parsePrice(textOf(element, 2000));
          else if (field === 'number') item.number = parseNumber(textOf(element, 2000));
          else if (field === 'sold') item.sold = lib.parseSold(textOf(element, 2000));
          else if (field === 'rating') item.rating = lib.parseRating(textOf(element, 2000));
          else if (field === 'reviews') {
            // "리뷰 1,234"가 없어도 "★4.5 (1,234)"처럼 평점 뒤 괄호 수를 리뷰 수로 쓴다.
            const text = textOf(element, 2000);
            item.reviews = lib.parseReviews(text) || (lib.parseRating(text)?.reviews ?? null);
          }
          else if (field === 'discount') item.discount = lib.parseDiscount(textOf(element, 2000));
          else if (field === 'recent_sales') item.recent_sales = lib.parseRecentSales(textOf(element, 2000));
          else if (field === 'attributes') {
            item.attributes = {};
            for (const name of params.attributes || []) item.attributes[name] = attributeOf(element, name);
          }
        }
        for (const name of params.attributes || []) {
          if (!fields.includes('attributes')) item[`@${name}`] = attributeOf(element, name);
        }
        if (dedupeKeys) {
          // v0.3.0: 키가 모두 비어 있는 항목은 서로 "같은" 것이 아니라 "알 수 없는" 것이므로 보존한다.
          const values = dedupeKeys.map(k => item[k] ?? null);
          countKeys(dedupeStats, values);
          if (!values.some(usable)) { dedupeStats.skipped_records++; }
          else {
            dedupeStats.usable_records++;
            const key = JSON.stringify(values);
            if (seen.has(key)) { duplicates++; continue; }
            seen.add(key);
          }
        }
        items.push(item);
      }
      const offset = params.offset || 0;
      const limit = params.limit || 100;
      const result = {items: items.slice(offset, offset + limit), total, matched: items.length, duplicates_removed: duplicates,
        offset, next_offset: offset + limit < items.length ? offset + limit : null};
      if (dedupeKeys) result.dedupe = finishDedupe(dedupeStats, elements.length, duplicates);
      return result;
    }

    // ---------- records 모드 ----------
    /** 형제 요소의 구조 서명. 태그와 앞 두 클래스로 반복 구조를 식별한다. */
    const signatureOf = element => element.tagName + (element.classList.length
      ? '.' + [...element.classList].slice(0, 2).join('.') : `#c${element.children.length}`);
    /** 컨테이너 아래에서 같은 서명을 가진 형제가 3개 이상이고 내용이 있는 그룹을 찾는다. */
    function detectItems(container) {
      const candidates = [];
      const queue = [container];
      let visited = 0;
      while (queue.length && visited < 4000) {
        const element = queue.shift();
        visited++;
        const groups = new Map();
        for (const child of element.children) {
          if (skipTags.has(child.tagName) || !isVisible(child)) continue;
          const signature = signatureOf(child);
          if (!groups.has(signature)) groups.set(signature, []);
          groups.get(signature).push(child);
          queue.push(child);
        }
        for (const [signature, members] of groups) {
          if (members.length < 3) continue;
          const texts = members.map(m => textOf(m, 400));
          const average = texts.reduce((sum, t) => sum + Math.min(t.length, 400), 0) / members.length;
          const rich = members.filter((m, i) => texts[i].length >= 12 && (m.querySelector('a[href], h1, h2, h3, h4, h5, img') || texts[i].length > 40)).length;
          if (rich < members.length * 0.6 || average < 12) continue;
          const priced = texts.filter(t => parsePrice(t)).length;
          // 항목 수, 평균 텍스트량, 가격 포함 비율로 상품/게시물 목록을 내비게이션 메뉴보다 우선한다.
          const score = members.length * (1 + Math.min(average, 300) / 300) * (1 + priced / members.length) *
            (element.closest('nav, header, footer, [role=navigation]') ? 0.3 : 1);
          candidates.push({score, members, signature, parent: element});
        }
      }
      candidates.sort((a, b) => b.score - a.score);
      return candidates;
    }
    /**
     * 항목 하나를 자동 필드로 읽는다.
     * 필수 후보: title/url/price/image(없으면 missing에 원인을 남긴다).
     * 선택 필드: original_price/discount/sold/recent_sales/rating/reviews/shipping(있을 때만 포함).
     * 텍스트는 textOf로 조립하므로 가격과 판매량이 붙어 출력되지 않는다.
     */
    function autoFields(item) {
      const fields = {};
      const missing = {};
      const heading = item.querySelector('h1, h2, h3, h4, h5, h6, [class*="title" i], [class*="name" i], [class*="subject" i]');
      const anchors = [...item.querySelectorAll('a[href]')].filter(a => textOf(a, 300));
      const longest = anchors.slice().sort((a, b) => textOf(b, 300).length - textOf(a, 300).length)[0];
      const image = item.querySelector('img');
      const text = textOf(item, 20000);
      const title = clip(textOf(heading, 300) || heading?.getAttribute('title') || textOf(longest, 300) || image?.getAttribute('alt') || text.split(/\s{2,}/)[0], 300);
      if (title) fields.title = title; else missing.title = 'selector_no_match';
      const link = (heading && heading.closest('a[href]')) || heading?.querySelector('a[href]') || longest || anchors[0];
      if (link) fields.url = link.href; else missing.url = 'selector_no_match';
      // 가격 요소를 먼저 찾고(현재가/원가 구분), 없으면 항목 전체 텍스트에서 첫 가격을 쓴다.
      const priceNodes = [...item.querySelectorAll('[class*="price" i], [class*="prc" i], [class*="amount" i], [class*="cost" i], [itemprop="price"]')]
        .filter(node => !node.querySelector('[class*="price" i], [class*="prc" i]'));
      const struck = node => !!node.closest('del, s, strike, [class*="original" i], [class*="before" i], [class*="regular" i], [class*="list-price" i], [class*="was" i], [class*="old" i]') ||
        /line-through/.test(getComputedStyle(node).textDecorationLine || '');
      let current = null;
      let original = null;
      for (const node of priceNodes) {
        const price = parsePrice(textOf(node, 200));
        if (!price) continue;
        if (struck(node)) { if (!original) original = price; } else if (!current) current = price;
      }
      const inText = lib.findPrices(text, 4).map(p => ({raw: p.raw, value: p.value, currency: p.currency}));
      if (!current) current = original ? null : inText[0] || null;
      if (!current && original) { current = original; original = null; }
      if (current) fields.price = current; else missing.price = 'selector_no_match';
      const discount = lib.parseDiscount(text);
      if (!original && current && inText.length >= 2 && discount) {
        // 취소선 요소가 없어도 할인율 표기가 있으면 텍스트의 더 큰 가격을 원가로 본다.
        const higher = inText.find(p => p.value > current.value && p.currency === current.currency);
        if (higher) original = {raw: higher.raw, value: higher.value, currency: higher.currency};
      }
      if (original && current && original.value > current.value) fields.original_price = original;
      if (discount) fields.discount = discount.percent;
      else if (fields.original_price && current) fields.discount = Math.round((1 - current.value / fields.original_price.value) * 100);
      const shipping = lib.parseShipping(text);
      if (shipping) fields.shipping = shipping;
      const sold = lib.parseSold(text);
      if (sold) fields.sold = sold;
      // 기간 한정 구매량("100+ bought in past month")은 누적 판매량과 다른 지표이므로 따로 둔다.
      const recent = lib.parseRecentSales(text);
      if (recent) fields.recent_sales = recent;
      const rating = lib.parseRating(text);
      if (rating) {
        fields.rating = rating.value;
        if (rating.reviews) fields.reviews = rating.reviews;
      }
      const reviews = lib.parseReviews(text);
      if (reviews) fields.reviews = reviews;
      if (image) fields.image = attributeOf(image, 'src'); else missing.image = 'selector_no_match';
      fields.text = clip(text, 300);
      return {fields, missing};
    }
    /**
     * 사용자가 지정한 필드 명세로 항목을 읽는다(v0.3.0 확장).
     * 명세 키: selector, source, attr, type, all, regex, relation, closest, canonical, stable_id.
     * - source="self" 또는 selector=":self"/":scope"는 레코드 요소 자체를 읽는다(selector 생략과 같다).
     *   0.2.0에서는 ":scope"가 item.querySelectorAll에 걸리지 않아 null이 되는 함정이 있었다.
     * - relation="closest"는 선택 요소에서 가장 가까운 조상(closest 선택자, 기본 a[href])으로 대상을 바꾼다.
     *   레코드 경계 밖의 조상은 다른 카드의 값일 수 있으므로 null로 본다(relation_no_match).
     * - type="url"이고 attr이 없으면 자기 href → 가장 가까운 a[href](레코드 안) → 내부 첫 a[href] 순으로 찾고,
     *   자기 href가 아니면 출처를 sources[name]에 closest_link/inner_link로 남긴다.
     * - canonical=true는 URL을 {raw, canonical, stable_id, removed}로, canonical="compact"는 {canonical, stable_id}만 돌려준다
     *   (stable_id 정규식 지정 가능).
     * - explain=true면 필드별 {selector_matches, raw(200자), parse_status}를 돌려준다. raw는 비밀번호 입력값을 가린
     *   attributeOf/표시 텍스트만 담으므로 쿠키·토큰은 들어가지 않는다.
     * missing 사유: selector_no_match, relation_no_match, attribute_empty, text_empty, regex_no_match,
     *   parse_failed(숫자가 없는 원문), unsupported_locale_pattern(숫자는 있지만 해석기가 모르는 표현).
     */
    const EVIDENCE = {
      price: /[₩$€£¥]|원|USD|KRW|EUR|GBP|JPY|CNY/, rating: /★|평점|별점|별 ?5|만점|rating|rated|stars?|\/\s?5|out of 5|\d\s?점/i,
      sold: /sold|판매|구매|bought|orders?|주문/i, recent_sales: /bought|purchased|지난|최근|월 ?\d|주 ?\d|구매|판매/i,
      reviews: /review|rating|리뷰|후기|평가|상품평|\(\s?\d/i, discount: /%/, shipping: /배송|shipping|delivery/i, count: /\d/, number: /\d/,
    };
    function specifiedFields(item, specs, explain) {
      const fields = {};
      const missing = {};
      const sources = {};
      const details = {};
      for (const [name, rawSpec] of Object.entries(specs)) {
        const spec = typeof rawSpec === 'string' ? {selector: rawSpec} : rawSpec;
        const self = spec.source === 'self' || spec.selector === ':self' || spec.selector === ':scope' || !spec.selector;
        let targets = self ? [item] : select(item, spec.selector, true);
        const detail = {selector_matches: targets.length, raw: null, parse_status: null};
        if (spec.relation === 'closest') {
          const ancestor = spec.closest || 'a[href]';
          targets = targets.map(target => {
            let found;
            try { found = target.closest(ancestor); } catch (error) { throw {code: 'INVALID_SELECTOR', selector: ancestor, message: String(error.message || error)}; }
            return found && item.contains(found) ? found : null;
          }).filter(Boolean);
        }
        if (!targets.length) {
          fields[name] = null;
          missing[name] = detail.selector_matches ? 'relation_no_match' : 'selector_no_match';
          detail.parse_status = missing[name];
          if (explain) details[name] = detail;
          continue;
        }
        let status = null;
        const read = target => {
          let value;
          let source = null;
          if (spec.type === 'url' && !spec.attr) {
            // href 자동 탐색: 선택 요소 자체 → 가장 가까운 링크(레코드 안) → 내부 첫 링크. 제목을 감싸는 링크 구조에 대응한다.
            const own = target instanceof HTMLAnchorElement ? target.href : absoluteUrl(target.getAttribute('href'));
            if (own) { value = own; source = 'self'; }
            else {
              const up = target.closest('a[href]');
              const inner = target.querySelector('a[href]');
              if (up && item.contains(up)) { value = up.href; source = 'closest_link'; }
              else if (inner) { value = inner.href; source = 'inner_link'; }
            }
            if (value == null) { status = 'attribute_empty'; return null; }
          } else {
            value = spec.attr ? attributeOf(target, spec.attr) : textOf(target);
          }
          if (value == null || value === '') { status = spec.attr ? 'attribute_empty' : 'text_empty'; return null; }
          detail.raw = clip(String(value), 200);
          if (spec.regex) {
            let pattern;
            try { pattern = new RegExp(spec.regex, 'i'); } catch { throw {code: 'INVALID_REGEX', field: name}; }
            const match = String(value).match(pattern);
            value = match ? (match[1] ?? match[0]) : null;
            if (value == null) { status = 'regex_no_match'; return null; }
          }
          let parsed = value;
          if (spec.type === 'price') parsed = parsePrice(value);
          else if (spec.type === 'number') parsed = parseNumber(value);
          else if (spec.type === 'url') parsed = absoluteUrl(value);
          else if (spec.type === 'count') parsed = lib.parseCount(value);
          else if (spec.type === 'rating') parsed = lib.parseRating(value);
          // v0.3.0: 자동 필드와 같은 해석기를 지정 필드에서도 쓴다(카드 전체 텍스트를 돌려주지 않고 작은 객체만 돌려준다).
          else if (spec.type === 'sold') parsed = lib.parseSold(value);
          else if (spec.type === 'recent_sales') parsed = lib.parseRecentSales(value);
          else if (spec.type === 'reviews') parsed = lib.parseReviews(value) || (lib.parseRating(value)?.reviews ?? null);
          else if (spec.type === 'discount') parsed = lib.parseDiscount(value);
          else if (spec.type === 'shipping') parsed = lib.parseShipping(value);
          // 해석 실패 구분: 그 종류의 값이 있다는 근거(통화 기호, 평점/판매 키워드 등)가 원문에 있는데 못 읽었으면
          // unsupported_locale_pattern(해석기가 모르는 표현), 근거 자체가 없으면 parse_failed(그런 값이 없음)다.
          if (parsed == null) { status = (EVIDENCE[spec.type] || /\d/).test(String(value)) ? 'unsupported_locale_pattern' : 'parse_failed'; return null; }
          if (spec.type === 'url' && spec.canonical) {
            parsed = lib.canonicalUrl(parsed, {stableId: spec.stable_id, field: name});
            // canonical="compact"는 raw/removed를 빼고 {canonical, stable_id}만 돌려준다(긴 추적 URL의 토큰 절약).
            if (spec.canonical === 'compact') parsed = {canonical: parsed.canonical, stable_id: parsed.stable_id};
          }
          if (source && source !== 'self') sources[name] = source;
          return parsed;
        };
        const values = (spec.all ? targets : targets.slice(0, 1)).map(read);
        const value = spec.all ? values.filter(v => v != null) : values[0];
        fields[name] = value ?? null;
        if (value == null || (spec.all && !value.length)) missing[name] = status || 'text_empty';
        detail.parse_status = missing[name] || 'ok';
        if (explain) details[name] = detail;
      }
      return {fields, missing, sources, details};
    }

    let members;
    let detection = null;
    if (params.item) {
      members = select(root, params.item, true);
      if (!params.includeHidden) members = members.filter(isVisible);
      detection = {method: 'selector', item: params.item};
    } else {
      const candidates = detectItems(root);
      if (!candidates.length) {
        return {records: [], total: 0, matched: 0, detection: {method: 'auto', found: false, candidates: []}, duplicates_removed: 0,
          offset: 0, next_offset: null, quality: {confidence: 'none', coverage: {}, records: 0, source_records: 0, output_records: 0, deduplicated: 0, duplicate_ratio: 0}};
      }
      members = candidates[0].members;
      detection = {method: 'auto', found: true, signature: candidates[0].signature,
        container: registry.identify(candidates[0].parent), container_tag: candidates[0].parent.tagName.toLowerCase(),
        candidates: candidates.slice(0, 4).map(c => ({signature: c.signature, count: c.members.length, container: registry.identify(c.parent)}))};
    }
    if (textTest) members = members.filter(m => textTest(textOf(m, 20000)));
    const total = members.length;
    // 1) 먼저 모든 항목을 추출한다. 자동 dedupe 키(url 유무)는 전체를 본 뒤 정해야 레코드마다 키가 달라지지 않는다.
    const extractedAll = members.map((item, index) => {
      const extracted = params.fields ? specifiedFields(item, params.fields, params.explain) : autoFields(item);
      const record = {index, id: registry.identify(item), fields: extracted.fields};
      if (Object.keys(extracted.missing).length) record.missing = extracted.missing;
      if (extracted.sources && Object.keys(extracted.sources).length) record.sources = extracted.sources;
      if (params.explain && extracted.details) record.explain = extracted.details;
      return record;
    });
    // 2) 중복 제거: 키가 모두 비어 있는 레코드는 보존한다(0.2.0은 "[null]" 키로 전부 합쳐 레코드를 잃었다).
    let dedupeKeys = null;
    if (params.dedupe) {
      dedupeKeys = Array.isArray(params.dedupe) ? params.dedupe
        : extractedAll.some(r => usable(r.fields.url)) ? ['url'] : ['title', 'price'];
    }
    const dedupeStats = dedupeKeys ? startDedupe(dedupeKeys) : null;
    const records = [];
    const seen = new Set();
    let duplicates = 0;
    for (const record of extractedAll) {
      if (dedupeKeys) {
        const values = dedupeKeys.map(k => keyValue(record.fields[k] ?? null));
        countKeys(dedupeStats, values);
        if (!values.some(usable)) dedupeStats.skipped_records++;
        else {
          dedupeStats.usable_records++;
          const key = JSON.stringify(values);
          if (seen.has(key)) { duplicates++; continue; }
          seen.add(key);
        }
      }
      records.push(record);
    }
    const offset = params.offset || 0;
    const limit = params.limit || 50;
    // 3) 품질 지표: 필드 커버리지(값이 있는 출력 레코드 비율)에 더해 원본/출력 수, 제거 비율, 키 커버리지, 경고를 돌려준다.
    const coverage = {};
    const names = params.fields ? Object.keys(params.fields) : ['title', 'url', 'price', 'image'];
    for (const name of names) {
      coverage[name] = records.length ? Math.round(records.filter(r => usable(r.fields[name])).length / records.length * 100) / 100 : 0;
    }
    const key = params.fields ? names : ['title', 'url', 'price'];
    const core = key.length ? key.reduce((sum, name) => sum + (coverage[name] || 0), 0) / key.length : 0;
    let confidence = !records.length ? 'none' : core >= 0.8 ? 'high' : core >= 0.5 ? 'medium' : 'low';
    const warnings = [];
    const cap = level => { if (confidence === 'high') confidence = level; };
    const duplicateRatio = total ? Math.round(duplicates / total * 1000) / 1000 : 0;
    const percent = ratio => `${Math.round(ratio * 1000) / 10}%`;
    const dedupe = dedupeKeys ? finishDedupe(dedupeStats, total, duplicates) : null;
    if (dedupe) {
      const lowKeys = dedupeKeys.filter(k => dedupe.key_coverage[k] < 0.5);
      if (duplicateRatio >= 0.5) {
        warnings.push(`dedupe removed ${percent(duplicateRatio)} of records` +
          (lowKeys.length ? ` while ${lowKeys.map(k => `${k} coverage was ${percent(dedupe.key_coverage[k])}`).join(', ')}` : ''));
        cap('medium');
      }
      if (lowKeys.length) {
        if (duplicateRatio < 0.5) warnings.push(`dedupe key coverage is low: ${lowKeys.map(k => `${k}=${percent(dedupe.key_coverage[k])}`).join(', ')}`);
        cap('medium');
      }
    }
    // 핵심 필드가 출력 레코드 전체에서 같은 값이면 선택자가 카드 밖(페이지 공통 요소)을 읽었을 가능성이 크다.
    const uniformCheck = params.fields ? (dedupeKeys || []) : ['title', 'url'];
    for (const name of uniformCheck) {
      const values = records.map(r => r.fields[name]).filter(usable).map(v => JSON.stringify(v));
      if (records.length >= 3 && values.length === records.length && new Set(values).size === 1) {
        warnings.push(`all ${records.length} records have the same ${name}`);
        cap('medium');
      }
    }
    // 필드별 누락 사유 집계: selector 실패와 해석 실패를 재조회 없이 구분한다.
    const missingCounts = {};
    for (const record of extractedAll) {
      for (const [name, reason] of Object.entries(record.missing || {})) {
        missingCounts[name] = missingCounts[name] || {};
        missingCounts[name][reason] = (missingCounts[name][reason] || 0) + 1;
      }
    }
    for (const [name, reasons] of Object.entries(missingCounts)) {
      if (reasons.selector_no_match === total && total) warnings.push(`field ${name}: selector matched nothing in any record`);
      else if (reasons.unsupported_locale_pattern && reasons.unsupported_locale_pattern >= Math.max(1, Math.ceil(total * 0.5))) {
        warnings.push(`field ${name}: ${reasons.unsupported_locale_pattern} records contain numbers the ${params.fields?.[name]?.type || 'text'} parser could not interpret`);
      }
    }
    const quality = {confidence, coverage, records: records.length, source_records: total, output_records: records.length,
      deduplicated: duplicates, duplicate_ratio: duplicateRatio};
    if (dedupe) quality.dedupe_key_coverage = dedupe.key_coverage;
    if (Object.keys(missingCounts).length) quality.missing = missingCounts;
    if (warnings.length) quality.warnings = warnings;
    const result = {records: records.slice(offset, offset + limit), total, matched: records.length, duplicates_removed: duplicates,
      detection, offset, next_offset: offset + limit < records.length ? offset + limit : null, quality};
    if (dedupe) result.dedupe = dedupe;
    return result;
  }
  try {
    return run();
  } catch (error) {
    if (error && error.code) return {error: error.code, ...error};
    throw error;
  }
}
