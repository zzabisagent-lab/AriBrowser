/**
 * 문서(프레임) 안에서 DOM 노드의 정체성을 기억하는 공용 ID 레지스트리.
 *
 * collector.js(의미 트리 수집), query.js(선택자 질의/레코드 추출), pagestate.js
 * (정렬·페이지 상태 감지)가 같은 레지스트리를 공유해야 어느 명령으로 얻은 요소든
 * 동일한 ID로 조작할 수 있다. Python 쪽은 이 파일 내용을 각 스크립트의
 * REGISTRY 자리 표시 주석 위치에 삽입한다. 따라서 이 파일은 `(prefix) => registry` 형태의
 * 함수 표현식 하나여야 한다.
 *
 * - DOM에 속성을 덧붙이지 않고 WeakMap으로 노드→ID를 기억한다. SPA가 같은 노드를
 *   유지하면 ID도 유지되고, 재생성된 노드와 새 문서는 반드시 새 ID를 받는다.
 * - 내부 ID 형식은 `{탭}-{프레임}-{문서UUID}-e{순번}`이며 디버깅 용도다. Agent에게는
 *   Python 쪽 IdRegistry가 짧은 ID(`t1e12`)로 바꿔 제공한다.
 */
(prefix) => {
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
      },
      /** 연결이 끊긴 노드의 항목을 정리해 Map이 무한히 자라지 않게 한다. */
      prune() {
        for (const [id, ref] of nodes) {
          if (!ref.deref()?.isConnected) nodes.delete(id);
        }
      }
    };
  }
  return window[key];
}
