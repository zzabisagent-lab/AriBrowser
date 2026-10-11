/**
 * 문서 시작 시 자동 재생을 억제한다. 네트워크 미디어 차단과 함께 사용하되
 * 사용자가 명시적으로 재생하는 컨트롤은 그대로 남겨 페이지 구조를 보존한다.
 */
(() => {
  /** 새로 추가되거나 autoplay 속성이 바뀐 미디어에서도 자동 재생 의도를 제거한다. */
  function suppress(root) {
    const elements = root.querySelectorAll?.('video[autoplay], audio[autoplay]') || [];
    for (const media of elements) {
      media.autoplay = false;
      media.pause();
      media.preload = 'none';
    }
  }
  new MutationObserver(() => suppress(document)).observe(document, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['autoplay']
  });
  document.addEventListener('DOMContentLoaded', () => suppress(document));
})();
