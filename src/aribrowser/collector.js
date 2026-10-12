/**
 * Chromium 문서 안에서 DOM, ARIA, 계산된 레이아웃을 의미 트리로 변환한다.
 * 노드 정체성은 registry.js의 WeakMap 레지스트리가 기억한다. 따라서 SPA의
 * 동일 노드는 ID를 유지하고, 재생성된 노드/새 문서는 반드시 새 ID를 받는다.
 * 이 코드는 페이지 데이터를 읽는 코드이며 페이지 텍스트를 명령으로 실행하지 않는다.
 * 반환하는 ID는 긴 내부 ID이며 Python 쪽에서 짧은 Agent용 ID로 변환된다.
 */
({prefix, maxNodes = 50000, maxElements = 20000, maxHiddenNodes = 5000, includeDecorative = false}) => {
  // 공용 레지스트리(registry.js)가 Python에서 아래 자리에 삽입된다.
  const registry = (/*REGISTRY*/)(prefix);
  registry.prune();
  const output = [];
  let visited = 0;
  let hiddenVisited = 0;
  let truncated = false;
  let hiddenTruncated = false;
  // 전송량이 성능을 좌우하므로 기본값과 같은 상태/빈 필드는 생략한 압축 형태로 만들고
  // Python(expand_element)이 전체 스키마로 되돌린다. 결과는 JSON 문자열로 반환한다.
  // CDP의 구조화 직렬화는 대형 객체에서 문자열 전달보다 수 배 느리기 때문이다.
  const stateDefaults = {visible: true, in_viewport: true, occluded: false, disabled: false, readonly: false,
    selected: false, checked: false, expanded: null, invalid: false, required: false, focused: false,
    accessibility_hidden: false, actionable: true};
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'HEAD', 'TEMPLATE', 'META', 'LINK']);
  const interactiveRoles = new Set(['link', 'button', 'textbox', 'searchbox', 'combobox', 'checkbox',
    'radio', 'slider', 'spinbutton', 'tab', 'menuitem', 'switch', 'option', 'treeitem']);
  // 구조 컨테이너 이름에 모든 후손 텍스트를 반복하면 대형 페이지의 출력량이 폭증한다.
  const containers = new Set(['main', 'navigation', 'banner', 'contentinfo', 'complementary', 'form',
    'table', 'rowgroup', 'list', 'group', 'dialog', 'alertdialog', 'region', 'article', 'iframe']);
  const roles = {A: 'link', BUTTON: 'button', TEXTAREA: 'textbox', SELECT: 'combobox',
    OPTION: 'option', H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading', H5: 'heading', H6: 'heading',
    NAV: 'navigation', MAIN: 'main', HEADER: 'banner', FOOTER: 'contentinfo', ASIDE: 'complementary',
    FORM: 'form', FIELDSET: 'group', UL: 'list', OL: 'list', LI: 'listitem', TABLE: 'table',
    THEAD: 'rowgroup', TBODY: 'rowgroup', TFOOT: 'rowgroup', TR: 'row', TH: 'columnheader', TD: 'cell',
    IMG: 'image', VIDEO: 'video', AUDIO: 'audio', CANVAS: 'canvas', SVG: 'image',
    IFRAME: 'iframe', DIALOG: 'dialog', DETAILS: 'group', SUMMARY: 'button', P: 'paragraph',
    BLOCKQUOTE: 'blockquote', PRE: 'code', LABEL: 'label', LEGEND: 'label', ARTICLE: 'article',
    SECTION: 'region', PROGRESS: 'progressbar', METER: 'meter', OUTPUT: 'status', HR: 'separator'};

  /** 공백만 정규화하고 문자열 길이는 별도의 text_truncated 표시와 함께 제한한다. */
  const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
  /** ShadowRoot 안의 ARIA 참조는 동일 트리 범위에서 먼저 해석한다. */
  const references = (element, attr) => normalize(element.getAttribute(attr)).split(' ').filter(Boolean)
    .map(id => element.getRootNode().getElementById?.(id)?.textContent || '').join(' ');
  /** 브라우저 AX 트리가 제공되지 않는 숨김 노드 등에 쓸 의미 역할을 계산한다. */
  function roleOf(element) {
    if (element.getAttribute('role')) return element.getAttribute('role').split(' ')[0];
    if (element.tagName === 'INPUT') {
      return ({checkbox: 'checkbox', radio: 'radio', range: 'slider', number: 'spinbutton',
        button: 'button', submit: 'button', reset: 'button', image: 'button', file: 'file',
        hidden: 'hidden', search: 'searchbox'})[element.type] || 'textbox';
    }
    if (element.isContentEditable) return 'textbox';
    if (element.tagName === 'A' && !element.hasAttribute('href')) return 'text';
    if (element.tagName === 'TH' && element.scope === 'row') return 'rowheader';
    return roles[element.tagName] || (element.tabIndex >= 0 || element.onclick ? 'button' : 'text');
  }
  /** 부모 우선으로 탐색하여 영역·폼·표의 소속과 깊이를 보존한다. */
  function visit(element, parent, groups, ancestorHidden) {
    if (++visited > maxNodes || output.length >= maxElements) { truncated = true; return; }
    if (skip.has(element.tagName)) return;
    const role = roleOf(element);
    const css = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const hidden = ancestorHidden || css.display === 'none' || css.contentVisibility === 'hidden' || Number(css.opacity) === 0;
    // 숨겨진 메가 메뉴/템플릿이 수만 개 노드를 가질 수 있어 숨김 서브트리에는 별도 상한을 둔다.
    if (hidden && ++hiddenVisited > maxHiddenNodes) { hiddenTruncated = true; return; }
    const visible = !hidden && css.visibility !== 'hidden' && css.visibility !== 'collapse'
      && Number(css.opacity) !== 0 && rect.width > 0 && rect.height > 0;
    const inViewport = visible && rect.bottom > 0 && rect.right > 0
      && rect.top < innerHeight && rect.left < innerWidth;
    const rawText = normalize(element.innerText || element.textContent);
    const directText = normalize([...element.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join(' '));
    const decorative = ['presentation', 'none'].includes(role) ||
      (element.tagName === 'IMG' && element.getAttribute('alt') === '' && !element.getAttribute('aria-label'));
    const hasSemantic = role !== 'text' || directText || (element.children.length === 0 && rawText);
    let nextParent = parent;
    let nextGroups = {...groups};
    if (hasSemantic && (!decorative || includeDecorative) && role !== 'hidden') {
      const id = registry.identify(element);
      const labelled = references(element, 'aria-labelledby');
      const labels = element.labels ? [...element.labels].map(l => normalize(l.textContent)) : [];
      const name = normalize(labelled || element.getAttribute('aria-label') || labels.join(' ') ||
        element.getAttribute('alt') || element.getAttribute('title') ||
        (['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)
          ? element.getAttribute('placeholder') || (role === 'button' ? element.value : '')
          : role === 'text' || containers.has(role) ? directText : rawText));
      const disabled = element.matches(':disabled') || element.closest('[inert], [aria-disabled="true"]') !== null;
      const readonly = !!element.readOnly || element.getAttribute('aria-readonly') === 'true';
      const modal = role === 'dialog' || role === 'alertdialog' || element.getAttribute('aria-modal') === 'true';
      const hitX = Math.max(0, Math.min(innerWidth - 1, (Math.max(0, rect.left) + Math.min(innerWidth, rect.right)) / 2));
      const hitY = Math.max(0, Math.min(innerHeight - 1, (Math.max(0, rect.top) + Math.min(innerHeight, rect.bottom)) / 2));
      const top = inViewport ? (element.getRootNode().elementFromPoint?.(hitX, hitY) || document.elementFromPoint(hitX, hitY)) : null;
      const occluded = inViewport && !!top && top !== element && !element.contains(top);
      const state = {
        visible, in_viewport: inViewport, occluded, disabled, readonly,
        selected: !!element.selected || element.getAttribute('aria-selected') === 'true' ||
          ['page', 'true', 'step'].includes(element.getAttribute('aria-current')),
        checked: element.indeterminate ? 'mixed' : element.getAttribute('aria-checked') === 'mixed'
          ? 'mixed' : !!element.checked || element.getAttribute('aria-checked') === 'true',
        expanded: element.hasAttribute('aria-expanded') ? element.getAttribute('aria-expanded') === 'true'
          : element.tagName === 'DETAILS' ? element.open : null,
        invalid: (element.willValidate && !element.validity.valid) ||
          (element.hasAttribute('aria-invalid') && element.getAttribute('aria-invalid') !== 'false'),
        required: !!element.required || element.getAttribute('aria-required') === 'true',
        focused: document.activeElement === element || element.getRootNode().activeElement === element,
        accessibility_hidden: element.closest('[aria-hidden="true"]') !== null,
        actionable: visible && !disabled && !occluded && css.pointerEvents !== 'none'
      };
      const actions = [];
      if ((interactiveRoles.has(role) || role === 'file' || element.onclick) && !disabled) actions.push('click', 'hover');
      if (['textbox', 'searchbox', 'spinbutton'].includes(role) && !readonly && !disabled) actions.push('fill', 'clear');
      if (['checkbox', 'radio', 'switch'].includes(role) && !disabled) actions.push('check');
      if (role === 'combobox' && element.tagName === 'SELECT' && !disabled) actions.push('select');
      if (role === 'file' && !disabled) actions.push('upload');
      if (role === 'form') actions.push('submit');
      if (modal) actions.push('close_modal');
      const isPassword = element.tagName === 'INPUT' && element.type === 'password';
      const value = isPassword ? (element.value ? '[REDACTED]' : '') : element.isContentEditable
        ? normalize(element.textContent) : 'value' in element ? element.value : null;
      if (['form', 'table', 'group', 'list'].includes(role)) nextGroups[role] = id;
      const media = ['image', 'video', 'audio', 'canvas'].includes(role) ? {
        alt: element.getAttribute('alt') || name,
        source: element.currentSrc || element.src || null,
        size: {width: element.naturalWidth || element.videoWidth || element.width?.baseVal?.value || rect.width,
          height: element.naturalHeight || element.videoHeight || element.height?.baseVal?.value || rect.height},
        loaded: element.tagName === 'IMG' ? element.complete && element.naturalWidth > 0 :
          ['VIDEO', 'AUDIO'].includes(element.tagName) ? element.readyState > 0 : true,
        rendered: false,
        needs_visual: ['canvas', 'video'].includes(role) || element.tagName === 'SVG' ||
          /chart|map|그래프|지도/i.test(name + ' ' + element.className),
        context: normalize(element.closest('figure')?.querySelector('figcaption')?.textContent || labels.join(' ')).slice(0, 500)
      } : null;
      const text = ['text', 'paragraph', 'heading', 'cell', 'columnheader', 'rowheader', 'label', 'code',
        'button', 'link', 'alert', 'status', 'option'].includes(role) ? (role === 'text' ? directText : rawText) : directText;
      // 압축 항목: 생략된 키는 Python이 기본값으로 채운다(expand_element 참고).
      const item = {id, role, tag: element.tagName.toLowerCase(),
        b: [Math.round(rect.x), Math.round(rect.y), Math.round(rect.width), Math.round(rect.height)]};
      if (parent) item.parent = parent;
      if (name) item.name = name.slice(0, 4000);
      if (text !== name) item.text = text.slice(0, 8000);  // 이름과 같으면 생략, 다르면 빈 문자열도 전송
      if (text.length > 8000 || name.length > 4000) item.text_truncated = true;
      if (value != null && value !== '') item.value = value;
      if (labels.length) item.labels = labels;
      const description = normalize(references(element, 'aria-describedby'));
      if (description) item.description = description;
      if (element.validationMessage) item.validation_message = element.validationMessage;
      if (element.href) item.href = element.href;
      if (typeof element.type === 'string' && element.type && ['INPUT', 'BUTTON', 'SELECT', 'TEXTAREA'].includes(element.tagName)) item.input_type = element.type;
      const diff = {};
      for (const key of Object.keys(state)) if (state[key] !== stateDefaults[key]) diff[key] = state[key];
      if (Object.keys(diff).length) item.state = diff;
      if (actions.length) item.actions = actions;
      if (Object.keys(nextGroups).length) item.groups = nextGroups;
      const emphasis = Number(css.fontWeight) >= 600;
      const position = modal ? 'modal' : css.position === 'fixed' ? 'fixed' : null;
      if (emphasis || state.invalid || role === 'alert' || position) {
        item.style = {emphasis: emphasis ? 'strong' : 'normal', meaning: state.invalid || role === 'alert' ? 'warning' : 'normal',
          visibility: visible ? 'visible' : 'hidden', position: position || 'flow'};
      }
      if (media) item.media = media;
      for (const [key, attr] of [['row_index', 'aria-rowindex'], ['column_index', 'aria-colindex'], ['set_size', 'aria-setsize'], ['position_in_set', 'aria-posinset']]) {
        const attribute = element.getAttribute(attr);
        if (attribute != null) item[key] = attribute;
      }
      output.push(item);
      nextParent = id;
    }
    // open Shadow DOM을 동일한 의미 트리로 순회한다. iframe은 호스트에서 별도로 순회한다.
    if (element.shadowRoot) {
      for (const child of element.shadowRoot.children) visit(child, nextParent, nextGroups, hidden);
    }
    for (const child of element.children) {
      if (visited > maxNodes) break;
      visit(child, nextParent, nextGroups, hidden || (element.tagName === 'DETAILS' && !element.open && child.tagName !== 'SUMMARY'));
    }
  }
  if (document.body) visit(document.body, null, {}, false);
  return JSON.stringify({elements: output, document_id: registry.documentId, visited, truncated,
    hidden_truncated: hiddenTruncated, hidden_visited: hiddenVisited,
    viewport: {width: innerWidth, height: innerHeight, scroll_x: scrollX, scroll_y: scrollY},
    ready_state: document.readyState});
}
