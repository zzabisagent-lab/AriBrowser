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
 */
(params) => {
  const registry = (/*REGISTRY*/)(params.prefix);
  const maxText = params.maxText || 500;
  const skipTags = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'META', 'LINK', 'HEAD']);
  /** 공백을 정규화하고 길이를 제한한다. 잘림 여부는 호출자가 길이로 판단한다. */
  const normalize = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
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
  /** 가격 문자열에서 숫자와 통화를 분리한다. 한국 원화와 주요 통화 기호를 인식한다. */
  const PRICE = /(?:(₩|\$|€|£|¥|USD|KRW|EUR|JPY)\s?)?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s?(원|KRW|USD|EUR|달러|€|\$)?/g;
  const parsePrice = text => {
    const source = normalize(text);
    // "200ml 6,200원"처럼 통화 표시가 없는 숫자(용량·수량)를 건너뛰고 첫 가격을 고른다.
    for (const match of source.matchAll(PRICE)) {
      if (!match[1] && !match[4]) continue;
      const digits = match[2].replace(/,/g, '') + (match[3] ? '.' + match[3] : '');
      const currency = match[1] || match[4];
      return {raw: match[0].trim(), value: Number(digits),
        currency: ({'₩': 'KRW', '원': 'KRW', '$': 'USD', '달러': 'USD', '€': 'EUR', '£': 'GBP', '¥': 'JPY'})[currency] || currency};
    }
    return null;
  };
  /** 숫자 필드: 쉼표를 제거한 첫 수를 반환한다. */
  const parseNumber = text => {
    const match = normalize(text).match(/-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?/);
    return match ? Number(match[0].replace(/,/g, '')) : null;
  };
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
      if (textTest) elements = elements.filter(el => textTest(normalize(el.innerText || el.textContent)) || textTest(nameOf(el)));
      const total = elements.length;
      const fields = params.fields || ['id', 'tag', 'text', 'href'];
      const items = [];
      const seen = new Set();
      let duplicates = 0;
      for (const element of elements) {
        const item = {};
        for (const field of fields) {
          if (field === 'id') item.id = registry.identify(element);
          else if (field === 'tag') item.tag = element.tagName.toLowerCase();
          else if (field === 'text') item.text = clip(element.innerText || element.textContent);
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
          } else if (field === 'price') item.price = parsePrice(element.innerText || element.textContent);
          else if (field === 'number') item.number = parseNumber(element.innerText || element.textContent);
          else if (field === 'attributes') {
            item.attributes = {};
            for (const name of params.attributes || []) item.attributes[name] = attributeOf(element, name);
          }
        }
        for (const name of params.attributes || []) {
          if (!fields.includes('attributes')) item[`@${name}`] = attributeOf(element, name);
        }
        if (params.dedupe) {
          const keys = Array.isArray(params.dedupe) ? params.dedupe : ['text', 'href'];
          const key = JSON.stringify(keys.map(k => item[k] ?? null));
          if (seen.has(key)) { duplicates++; continue; }
          seen.add(key);
        }
        items.push(item);
      }
      const offset = params.offset || 0;
      const limit = params.limit || 100;
      return {items: items.slice(offset, offset + limit), total, matched: items.length, duplicates_removed: duplicates,
        offset, next_offset: offset + limit < items.length ? offset + limit : null};
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
          const texts = members.map(m => normalize(m.innerText || m.textContent));
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
    /** 항목 하나를 자동 필드(title/url/price/shipping/image/text)로 읽는다. */
    function autoFields(item) {
      const fields = {};
      const missing = {};
      const heading = item.querySelector('h1, h2, h3, h4, h5, h6, [class*="title"], [class*="name"], [class*="subject"]');
      const anchors = [...item.querySelectorAll('a[href]')].filter(a => normalize(a.innerText || a.textContent));
      const longest = anchors.slice().sort((a, b) => normalize(b.innerText).length - normalize(a.innerText).length)[0];
      const image = item.querySelector('img');
      const text = normalize(item.innerText || item.textContent);
      const title = clip(heading?.innerText || heading?.textContent || longest?.innerText || image?.getAttribute('alt') || text.split(/\s{2,}|\n/)[0], 300);
      if (title) fields.title = title; else missing.title = 'no_match';
      const link = (heading && heading.closest('a[href]')) || heading?.querySelector('a[href]') || longest || anchors[0];
      if (link) fields.url = link.href; else missing.url = 'no_match';
      const priceNode = item.querySelector('[class*="price"], [class*="prc"], [class*="amount"], [class*="cost"]');
      const price = parsePrice(priceNode?.innerText || '') || parsePrice(text);
      if (price) fields.price = price; else missing.price = 'no_match';
      const shipping = text.match(/(?:배송비|배송)\s*:?\s*(무료|[\d,]+\s?원)|무료배송|free shipping/i);
      if (shipping) fields.shipping = shipping[1] ? shipping[1].replace(/\s/g, '') : shipping[0];
      if (image) fields.image = attributeOf(image, 'src'); else missing.image = 'no_match';
      fields.text = clip(text, 300);
      return {fields, missing};
    }
    /** 사용자가 지정한 필드 명세({selector, attr, type, all, regex})로 항목을 읽는다. */
    function specifiedFields(item, specs) {
      const fields = {};
      const missing = {};
      for (const [name, rawSpec] of Object.entries(specs)) {
        const spec = typeof rawSpec === 'string' ? {selector: rawSpec} : rawSpec;
        const targets = spec.selector ? select(item, spec.selector, true) : [item];
        if (!targets.length) { fields[name] = null; missing[name] = 'no_match'; continue; }
        const read = target => {
          let value = spec.attr ? attributeOf(target, spec.attr) : clip(target.innerText || target.textContent);
          if (value == null || value === '') return null;
          if (spec.regex) {
            let pattern;
            try { pattern = new RegExp(spec.regex, 'i'); } catch { throw {code: 'INVALID_REGEX', field: name}; }
            const match = String(value).match(pattern);
            value = match ? (match[1] ?? match[0]) : null;
          }
          if (value == null) return null;
          if (spec.type === 'price') return parsePrice(value);
          if (spec.type === 'number') return parseNumber(value);
          if (spec.type === 'url') return absoluteUrl(value);
          return value;
        };
        const values = (spec.all ? targets : targets.slice(0, 1)).map(read);
        const value = spec.all ? values.filter(v => v != null) : values[0];
        fields[name] = value ?? null;
        if (value == null || (spec.all && !value.length)) missing[name] = spec.attr ? 'attribute_empty' : spec.regex ? 'regex_no_match' : 'empty_text';
      }
      return {fields, missing};
    }

    let members;
    let detection = null;
    if (params.item) {
      members = select(root, params.item, true);
      if (!params.includeHidden) members = members.filter(isVisible);
      detection = {method: 'selector', item: params.item};
    } else {
      const candidates = detectItems(root);
      if (!candidates.length) return {records: [], total: 0, detection: {method: 'auto', found: false, candidates: []}, duplicates_removed: 0, offset: 0, next_offset: null};
      members = candidates[0].members;
      detection = {method: 'auto', found: true, signature: candidates[0].signature,
        container: registry.identify(candidates[0].parent), container_tag: candidates[0].parent.tagName.toLowerCase(),
        candidates: candidates.slice(0, 4).map(c => ({signature: c.signature, count: c.members.length, container: registry.identify(c.parent)}))};
    }
    if (textTest) members = members.filter(m => textTest(normalize(m.innerText || m.textContent)));
    const total = members.length;
    const records = [];
    const seen = new Set();
    let duplicates = 0;
    for (const [index, item] of members.entries()) {
      const extracted = params.fields ? specifiedFields(item, params.fields) : autoFields(item);
      const record = {index, id: registry.identify(item), fields: extracted.fields};
      if (Object.keys(extracted.missing).length) record.missing = extracted.missing;
      if (params.dedupe) {
        const keys = Array.isArray(params.dedupe) ? params.dedupe
          : extracted.fields.url != null ? ['url'] : ['title', 'price'];
        const key = JSON.stringify(keys.map(k => extracted.fields[k] ?? null));
        if (seen.has(key)) { duplicates++; continue; }
        seen.add(key);
      }
      records.push(record);
    }
    const offset = params.offset || 0;
    const limit = params.limit || 50;
    return {records: records.slice(offset, offset + limit), total, matched: records.length, duplicates_removed: duplicates,
      detection, offset, next_offset: offset + limit < records.length ? offset + limit : null};
  }
  try {
    return run();
  } catch (error) {
    if (error && error.code) return {error: error.code, ...error};
    throw error;
  }
}
