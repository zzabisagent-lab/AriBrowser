"""외부 사이트 변화 없이 실제 Chromium 기능을 재현하는 HTTP 테스트 환경."""

import io
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from aribrowser.browser import Browser
from aribrowser.engine import Config
from aribrowser.protocol import Protocol

HTML = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>주문 관리</title>
<style>body{font:16px sans-serif} #hidden{display:none} #offscreen{margin-top:1400px}
#covered{position:absolute;left:800px;top:20px;width:80px;height:30px}
#cover{position:absolute;left:800px;top:20px;width:80px;height:30px;background:black;z-index:10}
img{width:120px;height:80px} #drop{width:100px;height:50px;border:1px solid} </style>
<nav><a href="/other">다른 페이지</a><a href="/" aria-current="page">주문</a></nav>
<main><h1>주문 목록</h1><p>구조화된 본문</p>
<form id="form" onsubmit="event.preventDefault();document.querySelector('#notice').textContent='저장되었습니다.';document.querySelector('#name').value='';document.querySelector('#save').disabled=true">
<label for="name">고객 이름</label><input id="name" required aria-describedby="hint">
<span id="hint">고객의 성명을 입력하세요</span><input type="password" aria-label="비밀번호" value="secret123">
<label><input type="checkbox" id="agree">동의</label><label><input type="radio" name="kind">개인</label>
<label>상태<select id="status"><option value="all">전체</option><option value="paid">결제 완료</option></select></label>
<label>날짜<input type="date"></label><label>시간<input type="time"></label>
<input readonly aria-label="읽기 전용" value="고정"><button id="save">저장</button>
<input type="file" aria-label="첨부 파일" onchange="document.querySelector('#filename').textContent=this.files[0]?.name||''"><output id="filename"></output>
</form><div role="alert" id="notice"></div>
<table aria-label="주문표"><thead><tr><th>주문번호</th><th>고객</th><th>상태</th></tr></thead>
<tbody><tr><td>#1042</td><td>김민수</td><td>배송 중</td></tr><tr><td>#1041</td><td>이지영</td><td>결제 완료</td></tr></tbody></table>
<details><summary>추가 정보</summary><button>접힌 버튼</button></details>
<button id="hidden">숨긴 버튼</button><button id="covered">가려진 버튼</button><div id="cover"></div>
<button onclick="document.querySelector('#modal').showModal()">모달 열기</button>
<dialog id="modal"><h2>확인 창</h2><button onclick="this.closest('dialog').close()">닫기</button></dialog>
<button onclick="window.open('/other','_blank')">새 창</button>
<button onclick="document.querySelector('#notice').textContent=confirm('진행합니까?')?'수락':'거부'">대화상자</button>
<button ondblclick="this.textContent='두 번 클릭 완료'">두 번 클릭</button>
<button onmouseover="document.querySelector('#notice').textContent='호버 완료'">호버</button>
<button onclick="setTimeout(()=>document.querySelector('#notice').textContent='비동기 완료',250)">비동기</button>
<a href="/download" download>파일 받기</a>
<div id="drag" draggable="true" ondragstart="event.dataTransfer.setData('text/plain','ok')">드래그 원본</div>
<div id="drop" role="group" aria-label="드롭 대상" ondragover="event.preventDefault()" ondrop="event.preventDefault();this.textContent='드롭 완료'">대상</div>
<figure><img src="/one.png" alt="제품 사진"><figcaption>상품 대표 이미지</figcaption></figure>
<img src="/two.png" alt="다른 사진"><img src="/decorative.png" alt="">
<canvas aria-label="매출 차트" width="80" height="60"></canvas><video autoplay src="/movie.mp4" aria-label="설명 영상"></video>
<div id="shadow"></div><iframe title="내부 프레임" src="/frame"></iframe>
<iframe title="외부 프레임" src="http://localhost:PORT/frame"></iframe>
<div role="list" id="virtual" style="height:70px;overflow:auto"><div style="height:800px"><button id="vrow">가상 행 0</button></div></div>
<button id="offscreen">화면 밖 버튼</button></main>
<script>
document.querySelector('#shadow').attachShadow({mode:'open'}).innerHTML='<label>그림자 이름<input></label><button>그림자 버튼</button>';
document.querySelector('#virtual').onscroll=()=>document.querySelector('#vrow').textContent='가상 행 '+Math.floor(document.querySelector('#virtual').scrollTop/20);
document.querySelector('canvas').getContext('2d').fillRect(0,0,40,30);
</script></html>"""


# 상품 검색 결과 페이지: 반복 레코드, 정렬 select/탭, 필터 토글, 페이지네이션, 전체 건수,
# 비동기 재정렬(SPA)과 아무 일도 하지 않는 버튼을 제공해 추출/상태/검증 기능을 검사한다.
PRODUCTS = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>상품 검색</title>
<style>body{font:14px sans-serif} .prod{margin:4px;padding:4px;border:1px solid #ccc} .sort-tabs li.on a{font-weight:bold}
.pagination a[aria-current]{font-weight:bold}</style>
<header><nav><ul><li><a href="/">홈</a></li><li><a href="/products">상품</a></li><li><a href="/other">기타</a></li></ul></nav></header>
<main><h1>검색 결과</h1><p class="total">전체 (123)</p>
<form class="sort-form"><label>정렬 <select id="sort" name="sort"><option value="popular">인기순</option><option value="low">낮은가격순</option><option value="high">높은가격순</option></select></label>
<label>표시 <select id="per"><option>30개씩</option><option selected>60개씩</option><option>120개씩</option></select></label>
<label><input type="checkbox" id="ship" checked> 배송비 포함</label><label><input type="checkbox" id="free"> 무료배송만</label></form>
<ul class="sort-tabs"><li class="on"><a href="?sort=popular">인기순</a></li><li><a href="?sort=low">낮은가격순</a></li><li><a href="?sort=high">높은가격순</a></li></ul>
<ul id="products">
<li class="prod"><a class="name" href="/item/1">아크릴 보드 A</a> <span class="price">35,000원</span> <span class="ship">배송비 3,000원</span> <span class="seller">판매처 가</span><img src="/one.png" alt="A"></li>
<li class="prod"><a class="name" href="/item/2">아크릴 보드 B</a> <span class="price">15,580원</span> <span class="ship">무료배송</span> <span class="seller">판매처 나</span><img src="/two.png" alt="B"></li>
<li class="prod"><a class="name" href="/item/3">아크릴 보드 C</a> <span class="price">22,900원</span> <span class="ship">배송비 2,500원</span> <span class="seller">판매처 다</span></li>
<li class="prod"><a class="name" href="/item/4">아크릴 보드 D</a> <span class="price">99,000원</span> <span class="seller">판매처 라</span></li>
<li class="prod"><a class="name" href="/item/2">아크릴 보드 B</a> <span class="price">15,580원</span> <span class="ship">무료배송</span> <span class="seller">판매처 나</span></li>
<li class="prod"><a class="name" href="/item/5">아크릴 보드 E</a> <span class="price">48,000원</span> <span class="seller">판매처 마</span></li>
<li class="prod"><a class="name" href="/item/6">아크릴 물감 세트 120ml</a> <span>6,200원</span> <span class="seller">판매처 바</span></li>
</ul>
<nav class="pagination" aria-label="페이지"><a href="?page=1" aria-current="page">1</a> <a href="?page=2">2</a> <a href="?page=3">3</a> <a href="?page=2" rel="next">다음</a></nav>
<button id="noop">정렬 적용</button> <button id="async-sort">낮은가격순 비동기</button> <div id="status" role="status"></div></main>
<script>
document.querySelector('#async-sort').onclick = () => setTimeout(() => fetch('/api/slow').then(r => r.json()).then(() => {
  const list = document.querySelector('#products');
  const priceOf = li => parseInt((li.querySelector('.price') || li.querySelector('span')).textContent.replace(/,/g, ''));
  [...list.children].sort((a, b) => priceOf(a) - priceOf(b)).forEach(li => list.appendChild(li));
  document.querySelector('#sort').value = 'low';
  document.querySelector('#status').textContent = '낮은가격순 적용';
  history.pushState(null, '', '?sort=low&page=1');
}), 200);
</script></html>"""


@pytest.fixture(scope="session")
def site():
    """파일·오류·리디렉션·교차 출처 프레임을 제공하며 자원별 실제 요청 횟수를 센다."""
    hits = Counter()
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), "#2980b9").save(buffer, format="PNG")

    class Handler(BaseHTTPRequestHandler):
        """테스트 전용 응답을 제공하는 표준 라이브러리 서버 핸들러."""

        def do_GET(self):
            """URL에 따라 결정적 콘텐츠/상태/첨부 파일을 반환한다."""
            hits[self.path] += 1
            status, content_type, extra = 200, "text/html; charset=utf-8", {}
            if self.path == "/":
                body = HTML.replace("PORT", str(self.server.server_port)).encode()
            elif self.path.startswith("/products"):
                body = PRODUCTS.encode()
            elif self.path == "/api/slow":
                # SPA 비동기 로딩을 흉내 내는 느린 JSON 응답.
                time.sleep(0.4)
                content_type, body = "application/json", b'{"ok":true}'
            elif self.path == "/frame":
                body = '<label>프레임 입력<input></label><button>프레임 버튼</button>'.encode()
            elif self.path == "/other":
                body = '<title>다른 페이지</title><h1>이동 완료</h1>'.encode()
            elif self.path.endswith(".png"):
                content_type, body = "image/png", buffer.getvalue()
            elif self.path == "/download":
                content_type, body = "text/plain", b"AriBrowser download verification"
                extra["Content-Disposition"] = 'attachment; filename="report.txt"'
            elif self.path == "/redirect":
                status, body, extra = 302, b"", {"Location": "/other"}
            elif self.path == "/error":
                status, body = 500, b"<h1>server failure</h1>"
            elif self.path == "/auth":
                status, body = 401, b"<h1>authentication required</h1>"
            else:
                status, body = 404, b"<h1>not found</h1>"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            """테스트 출력에는 HTTP 접속 로그 대신 실패 원인만 남긴다."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"url": f"http://127.0.0.1:{server.server_port}", "hits": hits}
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
async def app(tmp_path, site):
    """테스트마다 독립 Chromium 프로필과 동일 프로토콜 진입점을 생성한다."""
    browser = Browser(Config(data_dir=tmp_path, no_sandbox=True, timeout_ms=3000))
    protocol = Protocol(browser)
    response = await protocol.handle({"id": 0, "command": "navigate", "params": {"url": site["url"], "mode": "full"}})
    assert response["ok"], response
    yield browser, protocol
    await browser.close()


async def command(protocol, command_name: str, **params):
    """프로토콜 성공 응답을 검사하고 결과 본문을 반환하는 테스트 보조 함수."""
    response = await protocol.handle({"id": 1, "command": command_name, "params": params})
    assert response["ok"], response
    return response["result"]


async def find_id(protocol, role: str | None = None, text: str = "", **params) -> str:
    """검색 결과에서 정확한 이름을 우선해 테스트가 잘못된 부모 요소를 조작하지 않게 한다."""
    result = await command(protocol, "find", role=role, text=text, **params)
    assert result["elements"], (role, text)
    return next((e["id"] for e in result["elements"] if e.get("name", "") == text), result["elements"][0]["id"])
