/**
 * 상품 상세 페이지 하나를 구조화한 요약(제목·가격·원가·할인·판매량·평점·리뷰·배송·재고·
 * 브랜드·판매자·옵션·이미지)으로 읽는다. `page.product` 명령과 `navigate.include=["product"]`가 쓴다.
 *
 * 사이트별 선택자 없이 동작하기 위해 신뢰도 순으로 세 출처를 합친다.
 * 1. 구조화 데이터: JSON-LD(schema.org Product/Offer/AggregateRating), 마이크로데이터(itemprop)
 * 2. 메타 태그: Open Graph(og:title/og:image), product:price:amount/currency, og:availability
 * 3. DOM 휴리스틱: h1, 가격 클래스/취소선 요소, 본문 텍스트의 판매량·평점·리뷰·배송 패턴
 * 각 필드에 어떤 출처에서 읽었는지 `sources`로 남겨 Agent가 신뢰도를 판단할 수 있게 한다.
 *
 * 페이지 데이터를 읽기만 하며 변경하지 않는다. 쿠키·저장소·네트워크 API에는 접근하지 않는다.
 * 반환하는 요소 ID는 긴 내부 ID이며 Python 쪽에서 짧은 ID로 변환된다.
 */
(params) => {
  const registry = (/*REGISTRY*/)(params.prefix);
  const lib = (/*TEXTLIB*/);
  const normalize = lib.normalize;
  const clip = (value, limit) => {
    const text = normalize(value);
    return text.length > limit ? text.slice(0, limit) : text;
  };
  const product = {};
  const sources = {};
  const set = (name, value, source) => {
    if (value == null || value === '' || (Array.isArray(value) && !value.length)) return false;
    if (product[name] != null) return false;
    product[name] = value;
    sources[name] = source;
    return true;
  };
  const toPrice = (amount, currency) => {
    const value = Number(String(amount ?? '').replace(/[^\d.]/g, ''));
    if (!Number.isFinite(value) || value <= 0) return null;
    return {raw: normalize(`${amount} ${currency || ''}`), value, currency: lib.currencyCode(currency) || null};
  };

  // ---------- 1. JSON-LD ----------
  const nodes = [];
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    try {
      const data = JSON.parse(script.textContent);
      const queue = Array.isArray(data) ? [...data] : [data];
      while (queue.length && nodes.length < 50) {
        const node = queue.shift();
        if (!node || typeof node !== 'object') continue;
        nodes.push(node);
        if (Array.isArray(node['@graph'])) queue.push(...node['@graph']);
        if (node.mainEntity) queue.push(node.mainEntity);
      }
    } catch { /* 잘못된 JSON-LD는 무시한다. */ }
  }
  const typeOf = node => [].concat(node['@type'] || []).map(String);
  const ld = nodes.find(node => typeOf(node).some(type => /Product/i.test(type)));
  if (ld) {
    set('title', clip(ld.name, 300), 'json-ld');
    set('brand', clip(typeof ld.brand === 'object' ? ld.brand?.name : ld.brand, 100), 'json-ld');
    set('sku', clip(ld.sku || ld.productID || ld.mpn, 100), 'json-ld');
    set('description', clip(ld.description, 300), 'json-ld');
    const images = [].concat(ld.image || []).map(image => typeof image === 'object' ? image.url || image.contentUrl : image).filter(Boolean);
    set('images', images.slice(0, 5), 'json-ld');
    const offers = [].concat(ld.offers || []).flatMap(offer => offer?.offers ? [].concat(offer.offers) : [offer]).filter(Boolean);
    const offer = offers.find(o => o.price != null || o.lowPrice != null) || offers[0];
    if (offer) {
      const price = toPrice(offer.price ?? offer.lowPrice, offer.priceCurrency);
      if (price) set('price', price, 'json-ld');
      if (offer.availability) set('availability', /InStock|PreOrder|LimitedAvailability/i.test(String(offer.availability)) ? 'in_stock' :
        /OutOfStock|SoldOut|Discontinued/i.test(String(offer.availability)) ? 'out_of_stock' : null, 'json-ld');
      if (offer.seller?.name) set('seller', clip(offer.seller.name, 100), 'json-ld');
    }
    const rating = ld.aggregateRating;
    if (rating) {
      const value = Number(rating.ratingValue);
      if (Number.isFinite(value)) set('rating', value, 'json-ld');
      const count = Number(rating.reviewCount ?? rating.ratingCount);
      if (Number.isFinite(count)) set('reviews', {raw: String(rating.reviewCount ?? rating.ratingCount), value: count, plus: false}, 'json-ld');
    }
  }

  // ---------- 2. 마이크로데이터와 메타 태그 ----------
  const scope = document.querySelector('[itemtype*="schema.org/Product" i]');
  if (scope) {
    const prop = name => scope.querySelector(`[itemprop="${name}"]`);
    const read = node => node ? (node.getAttribute('content') || node.getAttribute('href') || node.getAttribute('src') || lib.textOf(node, 300)) : null;
    set('title', clip(read(prop('name')), 300), 'microdata');
    const price = toPrice(read(prop('price')), read(prop('priceCurrency')));
    if (price) set('price', price, 'microdata');
    set('brand', clip(read(prop('brand')), 100), 'microdata');
    const ratingValue = Number(read(prop('ratingValue')));
    if (Number.isFinite(ratingValue) && ratingValue > 0) set('rating', ratingValue, 'microdata');
    const reviewCount = Number(String(read(prop('reviewCount')) || '').replace(/,/g, ''));
    if (Number.isFinite(reviewCount) && reviewCount > 0) set('reviews', {raw: String(reviewCount), value: reviewCount, plus: false}, 'microdata');
  }
  const meta = name => document.querySelector(`meta[property="${name}"], meta[name="${name}"]`)?.getAttribute('content') || null;
  // og:title이 사이트 이름과 같거나 og:type이 website면 상품명이 아니라 사이트 공통 제목이므로 h1을 우선한다.
  const visible = element => element instanceof Element && !lib.hidden(element) && element.getClientRects().length > 0;
  const heading = [...document.querySelectorAll('h1')].find(visible) || document.querySelector('[class*="product-title" i], [class*="productTitle" i], [class*="prod_tit" i], [class*="item-title" i]');
  const ogGeneric = !meta('og:title') || meta('og:title') === meta('og:site_name') || (meta('og:type') || '').toLowerCase() === 'website';
  if (heading && ogGeneric && set('title', clip(lib.textOf(heading, 300), 300), 'h1')) product.title_id = registry.identify(heading);
  set('title', clip(meta('og:title') || meta('twitter:title'), 300), 'meta');
  const metaPrice = toPrice(meta('product:price:amount') || meta('og:price:amount'), meta('product:price:currency') || meta('og:price:currency'));
  if (metaPrice) set('price', metaPrice, 'meta');
  if (meta('og:availability') || meta('product:availability')) {
    set('availability', lib.parseAvailability(meta('og:availability') || meta('product:availability')), 'meta');
  }
  set('images', [meta('og:image')].filter(Boolean), 'meta');
  set('description', clip(meta('og:description') || meta('description'), 300), 'meta');
  set('brand', clip(meta('product:brand') || meta('og:brand'), 100), 'meta');

  // ---------- 3. DOM 휴리스틱 ----------
  if (heading) {
    if (set('title', clip(lib.textOf(heading, 300), 300), 'h1') || !product.title_id) product.title_id = registry.identify(heading);
  }
  const main = document.querySelector('main, [role=main], #content, #container, body') || document.body;
  const priceNodes = [...main.querySelectorAll('[class*="price" i], [class*="prc" i], [class*="amount" i], [itemprop="price"], [data-price]')]
    .filter(node => visible(node) && !node.querySelector('[class*="price" i], [class*="prc" i]') && !node.closest('[class*="recommend" i], [class*="related" i], [class*="also" i], footer, nav, header'))
    .slice(0, 40);
  const struck = node => !!node.closest('del, s, strike, [class*="original" i], [class*="before" i], [class*="regular" i], [class*="list-price" i], [class*="was" i], [class*="old" i]') ||
    /line-through/.test(getComputedStyle(node).textDecorationLine || '');
  const candidates = [];
  for (const node of priceNodes) {
    const price = lib.parsePrice(lib.textOf(node, 200));
    if (!price) continue;
    const size = parseFloat(getComputedStyle(node).fontSize) || 0;
    candidates.push({node, price, size, struck: struck(node), top: node.getBoundingClientRect().top});
  }
  // 현재가: 취소선이 아닌 후보 중 글자 크기가 가장 큰 것(동률이면 위쪽). 원가: 취소선 후보.
  const current = candidates.filter(c => !c.struck).sort((a, b) => b.size - a.size || a.top - b.top)[0];
  const original = candidates.filter(c => c.struck).sort((a, b) => b.size - a.size || a.top - b.top)[0];
  if (current) {
    if (set('price', current.price, 'dom')) product.price_id = registry.identify(current.node);
    else if (product.price && product.price.value === current.price.value) product.price_id = registry.identify(current.node);
  }
  if (original && (!product.price || original.price.value > product.price.value)) set('original_price', original.price, 'dom');
  const bodyText = lib.textOf(main, 30000);
  // 원가는 취소선 요소에서만 읽는다. 본문의 다른 가격(다른 판매처·관련 상품)을 원가로 오인하지 않기 위해서다.
  const discount = lib.parseDiscount(bodyText.slice(0, 8000));
  if (discount) set('discount', discount.percent, 'text');
  else if (product.original_price && product.price) set('discount', Math.round((1 - product.price.value / product.original_price.value) * 100), 'computed');
  set('sold', lib.parseSold(bodyText), 'text');
  const rating = lib.parseRating(bodyText);
  if (rating) {
    set('rating', rating.value, 'text');
    if (rating.reviews) set('reviews', rating.reviews, 'text');
  }
  set('reviews', lib.parseReviews(bodyText), 'text');
  set('shipping', lib.parseShipping(bodyText), 'text');
  set('availability', lib.parseAvailability(bodyText.slice(0, 5000)), 'text');
  const sellerNode = [...main.querySelectorAll('a, span, div, p')].find(node => visible(node) && /^(판매자|판매처|스토어|seller|store|sold by|shop)\s*:?\s*\S/i.test(lib.textOf(node, 80)) && lib.textOf(node, 80).length < 60);
  if (sellerNode) set('seller', clip(lib.textOf(sellerNode, 80).replace(/^(판매자|판매처|스토어|seller|store|sold by|shop)\s*:?\s*/i, ''), 80), 'text');
  // 옵션: 네이티브 select와 sku/option 그룹(라벨 + 선택지 수).
  const options = [];
  for (const select of [...main.querySelectorAll('select')].filter(visible).slice(0, 10)) {
    // label 요소가 select를 감싸면 textContent에 선택지가 섞이므로 label의 직접 텍스트 노드만 읽는다.
    const ownText = label => [...label.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' ');
    const label = clip((select.labels && [...select.labels].map(l => ownText(l) || l.textContent).join(' ')) || select.getAttribute('aria-label') || select.name || select.id, 60);
    options.push({label: label || 'select', id: registry.identify(select), choices: select.options.length,
      selected: select.selectedIndex >= 0 ? clip(select.options[select.selectedIndex].text, 60) : null});
  }
  for (const group of [...main.querySelectorAll('[class*="sku-property" i], [class*="sku_prop" i], [class*="option-group" i], [class*="product-option" i], [class*="variant" i]')].filter(visible).slice(0, 10)) {
    const label = clip(group.querySelector('[class*="title" i], [class*="name" i], [class*="label" i], dt, legend, h3, h4, strong')?.textContent || '', 60);
    const items = group.querySelectorAll('li, button, [role=radio], [role=option], label, img');
    if (items.length >= 2 && !options.some(o => o.id === registry.identify(group))) {
      options.push({label: label || 'option', id: registry.identify(group), choices: items.length});
    }
  }
  if (options.length) set('options', options.slice(0, 10), 'dom');
  if (!product.images) {
    const images = [...main.querySelectorAll('img')].filter(img => visible(img) && img.naturalWidth >= 200 && img.naturalHeight >= 200)
      .slice(0, 5).map(img => img.currentSrc || img.src).filter(Boolean);
    if (images.length) set('images', images, 'dom');
  }
  if (!product.description) {
    const description = document.querySelector('[class*="description" i], [class*="detail" i] p, [itemprop="description"]');
    if (description) set('description', clip(lib.textOf(description, 300), 300), 'dom');
  }

  const detected = !!(product.title && product.price);
  const confidence = !product.title ? 'none' : (sources.price === 'json-ld' || sources.price === 'microdata' || sources.price === 'meta') ? 'high' :
    product.price ? 'medium' : 'low';
  return {detected, confidence, product, sources, url: location.href, structured_data: {json_ld: !!ld, microdata: !!scope, open_graph: !!meta('og:title')}};
}
