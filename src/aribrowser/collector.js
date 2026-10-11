/**
 * Chromium 문서 안에서 DOM, ARIA, 계산된 레이아웃을 의미 트리로 변환한다.
 * DOM 속성을 덧붙이지 않고 WeakMap으로 노드 정체성을 기억한다. 따라서 SPA의
 * 동일 노드는 ID를 유지하고, 재생성된 노드/새 문서는 반드시 새 ID를 받는다.
 * 이 코드는 페이지 데이터를 읽는 코드이며 페이지 텍스트를 명령으로 실행하지 않는다.
 */
({prefix, maxNodes = 50000, includeDecorative = false}) => {
  const key = '__aribrowser_v001__';
  if (!window[key]) {
    const nodeIds = new WeakMap();
    const nodes = new Map();
    const documentId = crypto.randomUUID ? crypto.randomUUID() :
      [...crypto.getRandomValues(new Uint8Array(16))].map(byte => byte.toString(16).padStart(2, '0')).join('');
    let sequence = 0;
    window[key] = {
      nodes, nodeIds, documentId,
      /** 노드가 살아 있는 동안 ID를 유지한다. WeakRef는 제거된 DOM의 메모리 누수를 막는다. */
      identify(node) {
        if (!nodeIds.has(node)) {
          const id = `${prefix}-${documentId}-e${++sequence}`;
          nodeIds.set(node, id);
          nodes.set(id, new WeakRef(node));
        }
        return nodeIds.get(node);
      },
      /** 제거/탐색 이후의 ID는 다른 요소로 재사용하지 않고 null로 반환한다. */
      get(id) {
        const node = nodes.get(id)?.deref();
        return node?.isConnected ? node : null;
      }
    };
  }
  const registry = window[key];
  for (const [id, ref] of registry.nodes) {
    if (!ref.deref()?.isConnected) registry.nodes.delete(id);
  }
  const output = [];
  let visited = 0;
  let truncated = false;
  const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'HEAD', 'TEMPLATE', 'META', 'LINK']);
  const interactiveRoles = new Set(['link', 'button', 'textbox', 'searchbox', 'combobox', 'checkbox',
    'radio', 'slider', 'spinbutton', 'tab', 'menuitem', 'switch', 'option', 'treeitem']);
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
    if (++visited > maxNodes) { truncated = true; return; }
    if (skip.has(element.tagName)) return;
    const role = roleOf(element);
    const css = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    const hidden = ancestorHidden || css.display === 'none' || css.contentVisibility === 'hidden' || Number(css.opacity) === 0;
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
          : role === 'text' ? directText : rawText));
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
      output.push({id, parent, role, tag: element.tagName.toLowerCase(), name: name.slice(0, 4000),
        text: text.slice(0, 8000), text_truncated: text.length > 8000 || name.length > 4000,
        value, labels, description: normalize(references(element, 'aria-describedby')),
        validation_message: element.validationMessage || '', href: element.href || null,
        input_type: element.type || null, state, actions, groups: nextGroups,
        bounds: {x: Math.round(rect.x * 100) / 100, y: Math.round(rect.y * 100) / 100,
          width: Math.round(rect.width * 100) / 100, height: Math.round(rect.height * 100) / 100},
        style: {emphasis: Number(css.fontWeight) >= 600 ? 'strong' : 'normal',
          meaning: state.invalid || role === 'alert' ? 'warning' : 'normal',
          visibility: visible ? 'visible' : 'hidden', position: modal ? 'modal' : css.position === 'fixed' ? 'fixed' : 'flow'},
        media, accessibility_source: 'dom-aria',
        row_index: element.getAttribute('aria-rowindex'), column_index: element.getAttribute('aria-colindex'),
        set_size: element.getAttribute('aria-setsize'), position_in_set: element.getAttribute('aria-posinset')});
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
  return {elements: output, document_id: registry.documentId, visited, truncated,
    viewport: {width: innerWidth, height: innerHeight, scroll_x: scrollX, scroll_y: scrollY},
    ready_state: document.readyState};
}
