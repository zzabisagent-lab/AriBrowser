/**
 * 브라우저 안에서 쓰는 텍스트 정규화·가격/판매량/평점 해석 공용 라이브러리.
 *
 * query.js(레코드 추출), product.js(상세 페이지 요약), readiness.js(로딩 품질 판정)가
 * 같은 해석 규칙을 공유해야 목록과 상세에서 읽은 가격이 서로 일치한다. Python 쪽은
 * 이 파일 내용을 각 스크립트의 TEXTLIB 자리 표시 주석 위치에 삽입하므로 이 파일은
 * `(() => ({...}))()` 형태의 객체 표현식 하나여야 한다.
 *
 * 핵심 문제는 "붙어서 출력되는 텍스트"다. 상품 카드는 `<span>₩12,345</span><span>1,000+ 판매</span>`
 * 처럼 인라인 요소를 공백 없이 이어 붙이므로 textContent/innerText가 "₩12,3451,000+ 판매"가
 * 되어 가격이 깨진다. 반대로 `<span>US $</span><span>12</span><span>.</span><span>34</span>`는
 * 붙여서 읽어야 "US $12.34"가 된다. textOf는 요소 경계마다 아래 규칙으로 공백을 넣는다.
 *
 * - 블록 요소(div/p/li/tr/td/h1~h6 등) 경계는 항상 공백이다.
 * - 인라인 경계는 앞 조각이 통화 기호·'.'·','로 끝나거나 뒤 조각이 '.'·','로 시작하면 붙인다.
 *   ("US $" + "12", "12" + "." + "34", "35" + ",000원")
 * - 그 밖의 경우(숫자+숫자, 숫자+문자, 문자+숫자)는 공백을 넣는다. "₩12,345" + "1,000+"가
 *   "12,3451,000"이라는 잘못된 천 단위 묶음이 되는 것을 막기 위해서다.
 */
(() => {
  const BLOCK = new Set(['ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'BR', 'DD', 'DETAILS', 'DIV', 'DL', 'DT',
    'FIELDSET', 'FIGCAPTION', 'FIGURE', 'FOOTER', 'FORM', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'HR', 'LI',
    'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'SUMMARY', 'TABLE', 'TBODY', 'TD', 'TFOOT', 'TH', 'THEAD', 'TR', 'UL']);
  const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'META', 'LINK', 'HEAD', 'SVG', 'IFRAME']);
  /** 통화 표기 → ISO 코드. 접두/접미 어느 쪽에 와도 같은 표를 쓴다. */
  const CURRENCY = {'₩': 'KRW', '원': 'KRW', 'KRW': 'KRW', '$': 'USD', 'US$': 'USD', 'US $': 'USD', 'USD': 'USD',
    '달러': 'USD', '€': 'EUR', 'EUR': 'EUR', '£': 'GBP', 'GBP': 'GBP', '¥': 'JPY', 'JPY': 'JPY', '엔': 'JPY',
    'C$': 'CAD', 'CAD': 'CAD', 'A$': 'AUD', 'AUD': 'AUD', '元': 'CNY', 'CNY': 'CNY', 'RMB': 'CNY', '위안': 'CNY'};
  const PREFIX = '(?:US\\s?\\$|C\\$|A\\$|₩|\\$|€|£|¥|USD|KRW|EUR|GBP|JPY|CNY|RMB)';
  const SUFFIX = '(?:원|KRW|USD|EUR|GBP|JPY|CNY|달러|엔|元|위안|€|\\$)';
  // 그룹: 1=접두 통화, 2=정수부(천 단위 구분 포함), 3=소수부, 4=접미 통화
  const PRICE = new RegExp(`(?:(${PREFIX})\\s?)?(\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.(\\d{1,2}))?(?!\\d)\\s?(${SUFFIX})?`, 'g');
  const GLUE_BEFORE = /[₩$€£¥.,]$|US\s?\$$/;
  const GLUE_AFTER = /^[.,]/;

  /** 공백을 하나로 접고 양끝을 자른다. */
  const normalize = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();

  /** display:none 등으로 숨겨진 요소인지 판단한다. 텍스트 노드의 부모 기준으로 호출한다. */
  const hidden = element => {
    if (!(element instanceof Element)) return false;
    if (element.checkVisibility) return !element.checkVisibility({visibilityProperty: true, contentVisibilityAuto: true});
    const css = getComputedStyle(element);
    return css.display === 'none' || css.visibility === 'hidden';
  };

  /**
   * 요소의 표시 텍스트를 경계 규칙에 따라 조립한다. limit 문자에 도달하면 멈춘다.
   * 숨겨진 하위 요소와 script/style은 제외한다. 결과는 normalize를 거친다.
   */
  const textOf = (root, limit = 20000) => {
    if (!root) return '';
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode(node) {
        if (node.nodeType === 1) {
          if (SKIP.has(node.tagName) || hidden(node)) return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_SKIP;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    let output = '';
    let previousParent = null;
    let blockBoundary = false;
    const blockBetween = (a, b) => {
      // 두 텍스트 노드의 부모 요소 사이에 블록 요소 경계가 있는지: 공통 조상(루트 포함)까지 올라가며 확인한다.
      const path = new Set();
      for (let node = a; node; node = node === root ? null : node.parentNode) path.add(node);
      for (let node = b; node; node = node === root ? null : node.parentNode) {
        if (path.has(node)) {
          for (let up = a; up && up !== node; up = up.parentNode) if (BLOCK.has(up.tagName)) return true;
          for (let up = b; up && up !== node; up = up.parentNode) if (BLOCK.has(up.tagName)) return true;
          return false;
        }
      }
      return true;
    };
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const piece = String(node.textContent || '').replace(/\s+/g, ' ');
      if (!piece.trim()) {
        // 공백만 있는 노드도 경계 정보는 남긴다(원문에 공백이 있었다는 뜻).
        if (output && !output.endsWith(' ')) output += ' ';
        continue;
      }
      const parent = node.parentNode;
      if (previousParent && parent !== previousParent) {
        blockBoundary = blockBetween(previousParent, parent);
        const trimmedOut = output.replace(/\s+$/, '');
        const glue = !blockBoundary && (GLUE_BEFORE.test(trimmedOut) || GLUE_AFTER.test(piece.trimStart()))
          && !piece.startsWith(' ') && !output.endsWith(' ');
        if (!glue && output && !output.endsWith(' ')) output += ' ';
      }
      output += piece;
      previousParent = parent;
      if (output.length >= limit) break;
    }
    const text = normalize(output);
    return text.length > limit ? text.slice(0, limit) : text;
  };

  /** 통화 표기를 ISO 코드로 바꾼다. 모르는 표기는 그대로 돌려준다. */
  const currencyCode = symbol => {
    if (!symbol) return null;
    const key = symbol.replace(/\s+/g, ' ').trim();
    return CURRENCY[key] || CURRENCY[key.replace(' ', '')] || key;
  };

  /**
   * 텍스트에서 가격을 모두 찾는다. 통화 표시가 없는 숫자(용량·수량·개수)는 가격으로 보지 않는다.
   * 각 항목은 {raw, value, currency, index}이며 문서 순서다.
   */
  const findPrices = (text, limit = 20) => {
    const source = normalize(text);
    const found = [];
    for (const match of source.matchAll(PRICE)) {
      if (!match[1] && !match[4]) continue;
      const digits = match[2].replace(/,/g, '') + (match[3] ? '.' + match[3] : '');
      const value = Number(digits);
      if (!Number.isFinite(value) || value <= 0) continue;
      found.push({raw: match[0].trim(), value, currency: currencyCode(match[1] || match[4]), index: match.index});
      if (found.length >= limit) break;
    }
    return found;
  };

  /** 첫 가격 하나. 없으면 null이다. */
  const parsePrice = text => {
    const prices = findPrices(text, 1);
    if (!prices.length) return null;
    const {raw, value, currency} = prices[0];
    return {raw, value, currency};
  };

  /** 쉼표를 제거한 첫 수. */
  const parseNumber = text => {
    const match = normalize(text).match(/-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?/);
    return match ? Number(match[0].replace(/,/g, '')) : null;
  };

  /** "1,000+", "1.2K", "3만", "10k+" 같은 수량 표기를 숫자와 plus 여부로 바꾼다. 단위 없는 소수(4.5)는 수량이 아니다. */
  const parseCount = raw => {
    const match = normalize(raw).match(/(\d[\d,]*(?:\.\d+)?)\s?([kKmM만천]?)\s?(\+?)/);
    if (!match) return null;
    let value = Number(match[1].replace(/,/g, ''));
    const unit = match[2];
    if (match[1].includes('.') && !unit) return null;
    if (unit === 'k' || unit === 'K' || unit === '천') value *= 1000;
    else if (unit === 'm' || unit === 'M') value *= 1000000;
    else if (unit === '만') value *= 10000;
    return {raw: normalize(raw), value: Math.round(value), plus: match[3] === '+'};
  };

  /**
   * 판매량: "1,000+ sold", "판매 2.3만", "구매 1,234", "1K+ orders".
   * 숫자와 키워드가 바로 붙어 있어야 하며 "판매 1위", "구매 전 필독"처럼 순위/안내 문구는 제외한다.
   */
  const NOT_COUNT = /^\s?(?:위|등|%|페이지|번째|개월|일|시간|분|년|월|차|호)/;
  const parseSold = text => {
    const source = normalize(text);
    // "판매 1위 ... 구매 345"처럼 첫 일치가 순위 문구일 수 있으므로 모든 일치를 순서대로 검사한다.
    for (const match of source.matchAll(/(\d[\d,]*(?:\.\d+)?\s?[kKmM만천]?\+?)\s?(?:개|건|pcs|pieces)?\s?(?:sold|판매됨|판매|구매|bought|orders?|주문)(?![가-힣a-z])/gi)) {
      if (NOT_COUNT.test(source.slice(match.index + match[1].length))) continue;
      const count = parseCount(match[1]);
      if (count) return count;
    }
    for (const match of source.matchAll(/(?:sold|판매량|판매|구매|bought|orders?|주문)\s*:?\s*(\d[\d,]*(?:\.\d+)?\s?[kKmM만천]?\+?)(?![\d,.])/gi)) {
      if (NOT_COUNT.test(source.slice(match.index + match[0].length))) continue;
      const count = parseCount(match[1]);
      if (count) return count;
    }
    return null;
  };

  /** 할인율: "-18%", "18% off", "18% 할인", "18%↓". */
  const parseDiscount = text => {
    const match = normalize(text).match(/(?:-\s?(\d{1,2})\s?%)|(?:(\d{1,2})\s?%\s?(?:off|할인|↓|세일|sale|dc))/i);
    if (!match) return null;
    return {raw: match[0].trim(), percent: Number(match[1] || match[2])};
  };

  /**
   * 평점: 키워드(평점/별점/rating/★)나 "/5", "점", "(리뷰 수)" 같은 문맥이 있는 0.0~5.0만 인정한다.
   * "0.5x200x300mm" 같은 치수의 소수를 평점으로 오인하지 않기 위해 맨 숫자만으로는 판정하지 않는다.
   */
  const parseRating = text => {
    const stripped = normalize(text).replace(PRICE, m => (/[₩$€£¥원]|USD|KRW/.test(m) ? ' ' : m));
    const value = '(5(?:\\.0{1,2})?|[0-4](?:\\.\\d{1,2})?)';
    const count = '(?:\\s?\\(\\s?(\\d[\\d,]*\\s?[kKmM만천]?\\+?)\\s?\\))?';
    const patterns = [
      new RegExp(`(?:평점|별점|상품평점|rating|rated|stars?)\\s*:?\\s*${value}(?![\\d.%])${count}`, 'i'),
      new RegExp(`★\\s?${value}(?![\\d.%])${count}`),
      new RegExp(`(?:^|[^\\d.])${value}\\s?(?:\\/\\s?5|점|stars?|★|out of 5)(?![\\d.%])${count}`, 'i'),
      new RegExp(`(?:^|[^\\d.x×])([0-4]\\.\\d{1,2}|5\\.0{1,2})\\s?\\(\\s?(\\d[\\d,]*\\s?[kKmM만천]?\\+?)\\s?\\)`),
    ];
    for (const pattern of patterns) {
      const match = stripped.match(pattern);
      if (!match) continue;
      const number = Number(match[1]);
      if (!Number.isFinite(number) || number > 5) continue;
      const rating = {raw: match[0].trim(), value: number};
      if (match[2]) {
        const reviews = parseCount(match[2]);
        if (reviews) rating.reviews = reviews;
      }
      return rating;
    }
    return null;
  };

  /** 리뷰 수: "1,234 reviews", "리뷰 1,234", "리뷰수 ( 6 )", "후기 (56)", "평가 2.3천". */
  const parseReviews = text => {
    const source = normalize(text);
    const match = source.match(/(\d[\d,]*(?:\.\d+)?\s?[kKmM만천]?\+?)\s*(?:개의\s*|개\s*|건의\s*|건\s*)?(?:reviews?|ratings?|리뷰|후기|평가|상품평)(?![가-힣])/i) ||
      source.match(/(?:reviews?|ratings?|review count|리뷰\s?수|리뷰|후기\s?수|후기|평가\s?수|평가|상품평)\s*:?\s*\(?\s?(\d[\d,]*(?:\.\d+)?\s?[kKmM만천]?\+?)\s?\)?(?![\d,.])/i);
    return match ? parseCount(match[1]) : null;
  };

  /** 배송 정보: 무료배송, 배송비 n원, free shipping, shipping: $n. */
  const parseShipping = text => {
    const source = normalize(text);
    const match = source.match(/무료\s?배송|free\s?shipping|free\s?delivery|(?:배송비|배송)\s*:?\s*(무료|[\d,]+\s?원)|shipping\s*:?\s*(\$?\s?[\d.,]+\s?(?:원|\$|USD)?)/i);
    if (!match) return null;
    const value = match[1] || match[2] || match[0];
    return normalize(value).replace(/\s+(?=원)/, '');
  };

  /** 재고/판매 상태. */
  const parseAvailability = text => {
    const source = normalize(text);
    if (/품절|sold\s?out|out of stock|재고\s?없음|일시\s?품절|단종/i.test(source)) return 'out_of_stock';
    if (/in stock|재고\s?있음|구매\s?가능|available|바로\s?구매/i.test(source)) return 'in_stock';
    return null;
  };

  return {normalize, hidden, textOf, findPrices, parsePrice, parseNumber, parseCount, parseSold, parseDiscount,
    parseRating, parseReviews, parseShipping, parseAvailability, currencyCode, PRICE, BLOCK, SKIP};
})()
