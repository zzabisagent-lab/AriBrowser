"""요청한 영역만 이미지로 만들고 선택적으로 로컬 OCR을 수행한다.

기본 화면에는 이미지 바이트가 없다. 렌더 결과는 로컬 PNG 경로로 반환하며,
외부 비전 서비스로 웹페이지나 로그인 정보를 자동 전송하지 않는다.
"""

import asyncio
import base64
import io
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from .errors import BrowserError

# 요소와 하위 이미지의 실제 선택된 URL을 수집한다. CSS 배경은 명시적 렌더 때만 포함한다.
MEDIA_URLS = """(root, area) => {
  const urls = new Set();
  function visit(el) {
    const rect = el.getBoundingClientRect();
    const included = !area || (rect.right > area.x && rect.bottom > area.y && rect.left < area.x + area.width && rect.top < area.y + area.height);
    if (included && el instanceof HTMLImageElement) {
      const src = el.currentSrc || el.src;
      if (src) urls.add(src);
    }
    for (const match of (included ? getComputedStyle(el).backgroundImage : '').matchAll(/url\\(["']?(.*?)["']?\\)/g)) {
      try { urls.add(new URL(match[1], document.baseURI).href); } catch {}
    }
    for (const child of el.children) visit(child);
    if (el.shadowRoot) for (const child of el.shadowRoot.children) visit(child);
  }
  visit(root);
  return [...urls];
}"""

LOAD_MEDIA = """async (root, area) => {
  const jobs = [];
  function visit(el) {
    const rect = el.getBoundingClientRect();
    const included = !area || (rect.right > area.x && rect.bottom > area.y && rect.left < area.x + area.width && rect.top < area.y + area.height);
    if (included && el instanceof HTMLImageElement && (!el.complete || !el.naturalWidth)) {
      const src = el.currentSrc || el.src;
      if (src) {
        jobs.push(new Promise(resolve => {
          const done = () => { el.removeEventListener('load', done); el.removeEventListener('error', done); resolve(); };
          el.addEventListener('load', done, {once:true}); el.addEventListener('error', done, {once:true});
          setTimeout(done, 5000);
          // srcset을 그대로 유지해 반응형 이미지 선택과 레이아웃을 보존한다.
          el.loading = 'eager'; el.src = src;
        }));
      }
    }
    for (const child of el.children) visit(child);
    if (el.shadowRoot) for (const child of el.shadowRoot.children) visit(child);
  }
  visit(root);
  await Promise.all(jobs);
}"""


async def load_media(browser, tab, element=None, area=None) -> dict:
    """선택 범위의 URL만 네트워크 허용 목록에 추가하고 보류한 이미지를 다시 로딩한다."""
    own_handle = element is None
    if own_handle:
        element = await tab.page.query_selector("body")
    if element is None:
        return {"requested": []}
    try:
        urls = await element.evaluate(MEDIA_URLS, area)
        browser.allowed[tab.profile].update(urls)
        await element.evaluate(LOAD_MEDIA, area)
        # CSS 배경은 브라우저 캐시를 채운 후 computed style을 재평가시킨다.
        await element.evaluate("""async (root, urls) => {
          await Promise.all(urls.map(url => new Promise(resolve => {
            const img = new Image(); const timer = setTimeout(resolve, 5000);
            img.onload = img.onerror = () => { clearTimeout(timer); resolve(); }; img.src = url;
          })));
        }""", urls)
        return {"requested": urls}
    finally:
        if own_handle:
            await element.dispose()


async def request_resource(browser, resource_id: str) -> dict:
    """관측된 HTTP(S) 자원을 GET으로 다시 요청해 보관하며 임의 메서드 재실행을 피한다."""
    resource = browser.resources.get(resource_id)
    if not resource:
        raise BrowserError("RESOURCE_NOT_FOUND", "자원 ID가 없습니다.", resource=resource_id)
    if urlparse(resource["url"]).scheme not in {"http", "https"}:
        raise BrowserError("INVALID_ARGUMENT", "HTTP(S) 자원만 다시 요청할 수 있습니다.")
    context = browser.contexts.get(resource["profile"])
    if not context:
        raise BrowserError("PROFILE_NOT_FOUND", "자원의 프로필이 닫혔습니다.")
    browser.allowed[resource["profile"]].add(resource["url"])
    response = await context.request.get(resource["url"], timeout=browser.config.timeout_ms)
    try:
        if not response.ok:
            raise BrowserError("RESOURCE_FAILED", "자원을 다시 요청하지 못했습니다.", status=response.status)
        content = await response.body()
        folder = browser.config.data_dir / "resources"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{resource_id}-{uuid.uuid4().hex[:8]}.bin"
        path.write_bytes(content)
        resource.update(status="fetched", path=str(path), bytes=len(content))
        return dict(resource)
    finally:
        await response.dispose()


async def render(browser, tab_id: str | None = None, **options) -> dict:
    """요소/영역/화면/전체 페이지 PNG를 preview·normal·original 해상도로 생성한다."""
    tab = browser.get_tab(tab_id)
    target = options.get("target", "element" if options.get("element") else "viewport")
    level = options.get("level", "normal")
    if target not in {"element", "viewport", "page", "region"} or level not in {"preview", "normal", "original"}:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 렌더 대상/해상도입니다.")
    clip = options.get("clip")
    if target == "region":
        if not isinstance(clip, dict) or set(clip) != {"x", "y", "width", "height"} or any(
            not isinstance(v, (int, float)) or not 0 <= v <= 100000 for v in clip.values()
        ) or not clip["width"] or not clip["height"]:
            raise BrowserError("INVALID_ARGUMENT", "region에는 x/y/width/height clip이 필요합니다.")
    element = None
    metadata = None
    try:
        if target == "element":
            element = await browser.element_handle(tab, options.get("element", ""))
            metadata = (await browser.detail(options["element"], tab.id))["element"]
            await element.scroll_into_view_if_needed()
        if options.get("load_media", True):
            if element:
                await load_media(browser, tab, element)
            else:
                # 교차 출처 프레임도 Agent가 요청한 전체 이미지 안에서 개별 로딩한다.
                for frame in tab.page.frames:
                    body = await frame.query_selector("body")
                    if body:
                        try:
                            area = None
                            if target != "page":
                                area = dict(clip) if target == "region" else {"x": 0, "y": 0, **tab.page.viewport_size}
                                if frame != tab.page.main_frame:
                                    host = await frame.frame_element()
                                    try:
                                        box = await host.bounding_box()
                                        if box:
                                            area["x"] -= box["x"]
                                            area["y"] -= box["y"]
                                    finally:
                                        await host.dispose()
                            await load_media(browser, tab, body, area)
                        finally:
                            await body.dispose()
        scale = "device" if level == "original" else "css"
        if element:
            original = None
            if level == "original" and metadata.get("tag") == "img":
                source = await element.evaluate("el => el.currentSrc || el.src")
                if urlparse(source).scheme in {"http", "https"}:
                    response = await browser.contexts[tab.profile].request.get(source)
                    try:
                        if response.ok:
                            original = await response.body()
                    finally:
                        await response.dispose()
                elif source.startswith("data:image/") and ";base64," in source:
                    original = base64.b64decode(source.split(",", 1)[1])
            if original:
                try:
                    with Image.open(io.BytesIO(original)) as picture:
                        buffer = io.BytesIO()
                        picture.save(buffer, format="PNG")
                        png = buffer.getvalue()
                except (OSError, ValueError):
                    # SVG 등 벡터 원본은 Chromium의 장치 해상도 래스터화로 반환한다.
                    png = await element.screenshot(type="png", scale=scale, animations="disabled")
            else:
                png = await element.screenshot(type="png", scale=scale, animations="disabled")
        else:
            png = await tab.page.screenshot(type="png", full_page=target == "page",
                                            clip=clip if target == "region" else None,
                                            scale=scale, animations="disabled")
    finally:
        if element:
            await element.dispose()
    with Image.open(io.BytesIO(png)) as picture:
        if level == "preview":
            picture.thumbnail((480, 480))
            buffer = io.BytesIO()
            picture.save(buffer, format="PNG")
            png = buffer.getvalue()
        dimensions = {"width": picture.width, "height": picture.height}
    folder = browser.config.data_dir / "renders"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"render-{uuid.uuid4().hex}.png"
    path.write_bytes(png)
    result = {"path": str(path), "mime_type": "image/png", "bytes": len(png),
              "size": dimensions, "target": target, "level": level, "rendered": True,
              "description": {"alt": metadata["media"].get("alt") if metadata and metadata.get("media") else None,
                              "accessible_name": metadata.get("name") if metadata else None,
                              "source": "page_metadata", "vision_analysis": "available_to_calling_agent"}}
    if options.get("base64", False):
        result["base64"] = base64.b64encode(png).decode("ascii")
    if options.get("ocr", False):
        result["ocr"] = await extract_text(path, options.get("ocr_language", "eng"))
    return result


async def extract_text(path: Path, language: str = "eng") -> dict:
    """설치된 Tesseract로 로컬 OCR을 실행하며 미설치/언어팩 누락을 명시적으로 반환한다."""
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "reason": "Tesseract를 설치하면 로컬 OCR을 사용할 수 있습니다."}
    if not isinstance(language, str) or not language or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+" for c in language):
        raise BrowserError("INVALID_ARGUMENT", "OCR 언어 코드는 eng 또는 kor+eng 형태입니다.")
    process = await asyncio.create_subprocess_exec(executable, str(path), "stdout", "-l", language,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        process.kill()
        await process.wait()
        raise
    return {"status": "completed" if process.returncode == 0 else "failed",
            "text": stdout.decode("utf-8", errors="replace"),
            "error": stderr.decode("utf-8", errors="replace") if process.returncode else None}
