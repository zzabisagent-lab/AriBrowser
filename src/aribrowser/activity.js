/**
 * 문서 시작 시 설치되어 화면 안정화 판단과 조작 검증에 쓰이는 활동 추적기.
 *
 * - DOM 변이(MutationObserver) 횟수와 마지막 변이 시각을 기록한다. `wait`의
 *   dom_stable_ms 조건과 page.stability.dom_quiet_ms가 이 값을 읽는다.
 * - `probes`는 조작(action) 직전에 요소에 설치한 일회성 이벤트 리스너가 실제로
 *   받은 이벤트 이름을 보관한다. Playwright 호출이 성공해도 페이지가 이벤트를
 *   받지 못한 경우(가려짐, 리스너 없음 등)를 구분하기 위한 근거다.
 *
 * 페이지 스크립트보다 먼저 실행되지만 페이지 데이터를 바꾸지 않으며, 전역 이름은
 * 충돌을 피하기 위해 접두사를 붙인다.
 */
(() => {
  const key = '__aribrowser_activity__';
  if (window[key]) return;
  const state = window[key] = {
    mutations: 0,
    lastMutation: performance.now(),
    installed: performance.now(),
    probes: new Map(),
    /** 조작 직전에 호출되어 token 단위로 관측할 이벤트 이름을 등록한다. */
    probe(target, token, names) {
      const record = {events: [], target: target instanceof Element ? target.tagName.toLowerCase() : 'document'};
      this.probes.set(token, record);
      for (const name of names) {
        // capture 단계에서 듣는다. 페이지가 stopPropagation을 호출해도 수신 사실은 기록된다.
        target.addEventListener(name, () => {
          if (!record.events.includes(name)) record.events.push(name);
        }, {capture: true, passive: true});
      }
      if (this.probes.size > 50) this.probes.delete(this.probes.keys().next().value);
    },
    /** 조작 뒤에 호출되어 수신한 이벤트 목록을 반환하고 항목을 정리한다. */
    read(token) {
      const record = this.probes.get(token);
      this.probes.delete(token);
      return record ? record.events : null;
    }
  };
  const observer = new MutationObserver(records => {
    state.mutations += records.length;
    state.lastMutation = performance.now();
  });
  // documentElement가 아직 없을 수 있는 시점이므로 document 자체를 관찰한다.
  observer.observe(document, {childList: true, subtree: true, attributes: true, characterData: true});
})();
