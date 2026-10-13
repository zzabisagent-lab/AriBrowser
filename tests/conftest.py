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


# v0.2.0 검증용 페이지들.
# MARKET: 인라인 요소가 공백 없이 붙은 상품 카드(가격·원가·할인·판매량·평점·리뷰·배송) — 텍스트 경계 규칙과 자동 필드 검증.
MARKET = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>마켓 검색</title>
<style>.card{margin:4px;padding:4px;border:1px solid #ccc} .orig{text-decoration:line-through}</style>
<header><nav><a href="/">홈</a><a href="/market">마켓</a></nav></header>
<main><h1>마켓 결과</h1><p class="total">총 4개 상품</p>
<ul id="cards">
<li class="card"><a class="tit" href="/product/1">무선 이어폰 A</a><div class="p"><span class="price">₩12,345</span><span class="orig">₩15,000</span><span class="dc">18%↓</span></div><span class="sold">1,000+ 판매</span><span class="star">★4.5 (1,234)</span><span class="ship">무료배송</span><img src="/one.png" alt="A"></li>
<li class="card"><a class="tit" href="/product/2">USB 케이블 B</a><div class="p"><span class="price"><span>US $</span><span>12</span><span>.</span><span>34</span></span></div><span class="sold">sold 2.3K</span><span class="star">4.8 (56)</span><img src="/two.png" alt="B"></li>
<li class="card"><a class="tit" href="/product/3">아크릴 판 0.5x200x300mm</a><div class="p"><span class="price">990원</span></div><span class="rv">상품리뷰 별점 4.7 리뷰수 ( 4 )</span></li>
<li class="card"><a class="tit" href="/product/4">판매 1위 키보드 D</a><div class="p"><span class="price">35,000</span><span>원</span></div><span class="sold">구매 345</span></li>
</ul></main><footer>고객센터 · 이용약관 · 개인정보처리방침</footer></html>"""

# PRODUCT_PAGE: JSON-LD/Open Graph/DOM(취소선 원가, 옵션, 판매자)이 섞인 상품 상세.
PRODUCT_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>무선 이어폰 A - 마켓</title>
<meta property="og:title" content="무선 이어폰 A"><meta property="og:type" content="product"><meta property="og:image" content="/one.png">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"무선 이어폰 A","brand":{"@type":"Brand","name":"아리"},"sku":"A-1","image":["/one.png"],
"offers":{"@type":"Offer","price":"12345","priceCurrency":"KRW","availability":"https://schema.org/InStock","seller":{"@type":"Organization","name":"아리스토어"}},
"aggregateRating":{"@type":"AggregateRating","ratingValue":"4.5","reviewCount":"1234"}}</script>
<main><h1>무선 이어폰 A</h1><div class="price-box"><span class="price" style="font-size:24px">12,345원</span> <del class="price-orig">15,000원</del> <em>18% 할인</em></div>
<p class="meta">1,000+ 구매 · 평점 4.5 (1,234) · 무료배송</p>
<label>색상 <select id="color"><option>검정</option><option>흰색</option></select></label>
<div class="sku-property"><div class="title">용량</div><ul><li>32GB</li><li>64GB</li></ul></div>
<p class="description">노이즈 캔슬링을 지원하는 무선 이어폰입니다.</p><button>장바구니</button></main></html>"""

# SHELL: SPA 루트가 비어 있다가 1.2초 뒤 채워지는 페이지(로딩 대기 → ready). SHELL_EMPTY: 끝까지 비어 있는 빈 껍데기.
SHELL = """<!doctype html><html><meta charset="utf-8"><title>로딩</title><div id="root"></div>
<script>setTimeout(() => { document.querySelector('#root').innerHTML = '<main><h1>늦게 채워진 상품</h1><p>' + '내용 '.repeat(60) + '</p><span class="price">9,900원</span></main>'; }, 1200);</script></html>"""
SHELL_EMPTY = """<!doctype html><html><meta charset="utf-8"><title>빈 껍데기</title><div id="root"></div><script>window.__app = {};</script></html>"""
# SHELL_CHROME: 제목 없음, 시맨틱 태그 없는 머리글/바닥글 링크만 있고 앱 루트는 채워지지 않은 껍데기(시스템 Chrome에서 본 AliExpress 상세 형태).
SHELL_CHROME = """<!doctype html><html><meta charset="utf-8"><title></title>
<div class="site-header"><a href="/">AliMarket</a> <a href="/help">도움말</a> <a href="/cart">장바구니</a> <a href="/orders">주문</a> <a href="/account">계정</a></div>
<div id="root"><div class="pdp-wrap"></div></div>
<div class="site-footer"><p>고객센터, 분쟁 및 신고, 서비스 약속 안내, 지적 재산권 침해 신고, 윤리 위반 신고, 투명성 센터, 반품정책</p>
<p>글로벌 사이트 Русский Portuguese Spanish French German Italian Dutch Turkish Japanese Korean Thai Arabic Hebrew Polish</p>
<p>Browse by Category 인기 전체 제품 프로모션 저가 상품 리뷰 블로그 판매자 포털 앱 다운로드 파트너 제휴 프로그램 개인정보 처리방침 이용약관</p>
<ul><li><a href="/a">a</a></li><li><a href="/b">b</a></li><li><a href="/c">c</a></li><li><a href="/d">d</a></li><li><a href="/e">e</a></li><li><a href="/f">f</a></li></ul></div>
<script>for (let i = 0; i < 80; i++) document.querySelector('#root > div').appendChild(document.createElement('span'));</script></html>"""
# v0.3.0 검증용 페이지.
# AMAZON: Amazon 한국어 검색 결과를 본뜬 카드. 링크가 h2를 감싸는 구조, 최댓값이 먼저 나오는 평점("별 5개 중 4.3개"),
# 최근 월 구매 배지, 추적 매개변수가 붙은 상품 URL(같은 ASIN을 다른 추적값으로 두 번), 링크가 없는 카드, h2 안에 링크가 있는 카드.
def amazon_card(index: int, asin: str, title: str, rating: str, reviews: str, recent: str | None, price: str, link: str = "outer") -> str:
    """카드 HTML 하나를 만든다. link=outer: <a><h2>, inner: <h2><a>, none: 링크 없음."""
    heading = f'<h2 class="a-size-base"><span>{title}</span></h2>'
    if link == "outer":
        heading = f'<a class="a-link-normal" href="/dp/{asin}/ref=sr_1_{index}?dib=eyJ2IjoiMSJ9&amp;keywords=amp&amp;qid=17600{index}&amp;sr=8-{index}">{heading}</a>'
    elif link == "inner":
        heading = f'<h2 class="a-size-base"><a class="a-link-normal" href="/dp/{asin}?th=1&amp;psc=1"><span>{title}</span></a></h2>'
    badge = f'<span class="recent">{recent}</span>' if recent else ""
    return (f'<div class="s-result-item" data-asin="{asin}"><div class="brand">AriSound</div>{heading}'
            f'<div class="rating"><span class="a-icon-alt">{rating}</span><span class="rc">{reviews}</span></div>{badge}'
            f'<div class="a-price"><span class="a-offscreen">{price}</span></div><img src="/one.png" alt="{title}"></div>')


AMAZON_CARDS = [
    amazon_card(1, "B07W3K92ZM", "Valeton Rushead Max 기타 헤드폰 앰프", "별 5개 중 4.3개", "3,900", "지난달 100개 이상 구매", "₩45,524"),
    amazon_card(2, "B079FHY8PV", "SONICAKE Mini Guitar Headphone Amp", "4.1 out of 5 stars", "2,700", "50+ bought in past month", "₩40,226"),
    amazon_card(3, "B079FGN89H", "SONICAKE US Madness 헤드폰 앰프", "5점 만점에 4.0점", "1,400", None, "₩37,543"),
    amazon_card(4, "B084JB519R", "NUX Mighty Plug MP-2 모델 2.5", "별 5개 중 4.5개", "1,300", None, "₩52,000", link="inner"),
    amazon_card(5, "B079FWQV3Z", "Donner Mini Guitar Headphone Amp", "별 5개 중 4.2개", "1,200", "최근 한 달간 50개 이상 구매", "₩45,591", link="none"),
    amazon_card(6, "B07W3K92ZM", "Valeton Rushead Max 기타 헤드폰 앰프", "별 5개 중 4.3개", "3,900", "지난달 100개 이상 구매", "₩45,524"),
] + [amazon_card(7 + i, f"B0FAKE{i:04d}", f"Generic Headphone Amp {i}", "별 5개 중 4.0개", "10", None, "₩30,000") for i in range(6)]
AMAZON = ("""<!doctype html><html lang="ko"><meta charset="utf-8"><title>Amazon.com : electric guitar headphone amp</title>
<link rel="canonical" href="/s?k=electric+guitar+headphone+amp">
<header><nav><a href="/">홈</a><a href="/cart">장바구니</a></nav></header>
<main><h1>결과</h1><div class="s-main-slot">""" + "".join(AMAZON_CARDS) + """</div></main>
<footer><a href="/help">도움말</a></footer></html>""")

# ECHO: 요청의 Accept-Language 헤더(서버 삽입)와 navigator.language, Intl 시간대를 보여 주는 페이지(로캘/시간대 검증).
ECHO = """<!doctype html><html><meta charset="utf-8"><title>echo</title><main><h1>환경 확인</h1>
<p>accept-language: <span id="al">ACCEPT_LANGUAGE</span></p><p>navigator.language: <span id="nl"></span></p>
<p>timezone: <span id="tz"></span></p><p>offset: <span id="off"></span></p><p>본문 """ + "내용 " * 40 + """</p></main>
<script>document.querySelector('#nl').textContent = navigator.language; document.querySelector('#tz').textContent = Intl.DateTimeFormat().resolvedOptions().timeZone;
document.querySelector('#off').textContent = String(new Date(Date.UTC(2026, 0, 1, 12)).getHours());</script></html>"""

BLOCKED = """<!doctype html><html><meta charset="utf-8"><title>Verify</title><p>Please verify you are human</p><iframe src="/captcha/_____tmd_____/punish" style="width:300px;height:200px"></iframe></html>"""
LOGIN = """<!doctype html><html lang="ko"><meta charset="utf-8"><title>로그인</title><form><label>아이디 <input name="id"></label><label>비밀번호 <input type="password" name="pw"></label><button>로그인</button></form></html>"""


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
            elif self.path.startswith("/market"):
                body = MARKET.encode()
            elif self.path == "/product/1":
                body = PRODUCT_PAGE.encode()
            elif self.path == "/shell":
                body = SHELL.encode()
            elif self.path == "/shell-empty":
                body = SHELL_EMPTY.encode()
            elif self.path == "/shell-chrome":
                body = SHELL_CHROME.encode()
            elif self.path.startswith("/amazon"):
                body = AMAZON.encode()
            elif self.path.startswith("/dp/"):
                body = ('<title>상품</title><h1>' + self.path + '</h1><p>' + '내용 ' * 40 + '</p>').encode()
            elif self.path == "/echo":
                body = ECHO.replace("ACCEPT_LANGUAGE", self.headers.get("Accept-Language", "")).encode()
            elif self.path == "/blocked":
                body = BLOCKED.encode()
            elif self.path == "/forbidden":
                status, body = 403, b"<h1>Access Denied</h1><p>Request blocked.</p>"
            elif self.path == "/login":
                body = LOGIN.encode()
            elif self.path.startswith("/captcha/"):
                body = b"<p>slide to verify</p>"
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
