"""전체 DOM 대신 페이지의 요약 정보를 만든다.

첫 접속에서는 변경 비교 기준이 없어 `changes` 모드도 사실상 전체 페이지를
반환했다. v0.1.0의 `navigate`는 기본으로 이 요약을 돌려주고, Agent가 필요할 때만
`snapshot`/`find`/`extract`로 상세를 조회한다. 요약에는 제목·URL·로딩 상태·HTTP
상태·주요 영역·제목 목록·오류 정보·요소 수와 생략된 요소 수, 그리고 추가 조회에
쓸 수 있는 명령 예시를 담는다.

v0.2.0: 오류 이벤트의 URL은 호스트+경로로 축약하고 종류별 개수로 접어 추적용 긴 URL이
토큰을 낭비하지 않게 했다. `kind`(페이지 종류 추정)에 따라 다음 명령 추천을 바꾼다.
"""

from collections import Counter

from .engine import short_url
from .errors import BrowserError

# 페이지 종류별 다음 조회 명령 추천. 상품 목록은 extract, 상세는 page.product를 먼저 권한다.
NEXT_BY_KIND = {
    "listing": [{"command": "extract", "params": {"limit": 20, "dedupe": True}},
                {"command": "page.state", "params": {}},
                {"command": "find", "params": {"text": "<검색어>", "role": "link", "limit": 20}}],
    "product": [{"command": "page.product", "params": {}},
                {"command": "snapshot", "params": {"mode": "main", "format": "text", "max_tokens": 3000}}],
    "login": [{"command": "find", "params": {"role": ["textbox", "button"], "limit": 20, "fields": ["role", "name", "input_type"]}}],
    "article": [{"command": "snapshot", "params": {"mode": "main", "format": "text", "max_tokens": 4000}}],
    "other": [{"command": "snapshot", "params": {"mode": "main", "format": "text", "limit": 200}},
              {"command": "find", "params": {"text": "<검색어>", "role": "link", "limit": 20}},
              {"command": "extract", "params": {"limit": 20}}],
}

# 페이지 구조를 설명하는 랜드마크 역할. 이름이 없는 region/article은 제외한다.
LANDMARKS = {"banner", "navigation", "main", "complementary", "contentinfo", "form", "search",
             "dialog", "alertdialog", "region", "article", "tablist", "list", "table"}
NAMED_ONLY = {"region", "article", "list", "table"}


def heading_level(element: dict) -> int | None:
    """h1~h6 태그에서 제목 수준을 읽는다. ARIA heading은 수준을 알 수 없으면 None이다."""
    tag = element.get("tag") or ""
    if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
        return int(tag[1])
    return None


def page_summary(state: dict, events: list[dict], options: dict | None = None, kind: str | None = None) -> dict:
    """관측 상태와 최근 이벤트로 Agent가 다음 행동을 고를 수 있는 요약을 만든다. kind는 readiness의 페이지 종류 추정이다."""
    options = options or {}
    max_regions = options.get("max_regions", 20)
    max_headings = options.get("max_headings", 12)
    for key, value in (("max_regions", max_regions), ("max_headings", max_headings)):
        if type(value) is not int or not 0 <= value <= 200:
            raise BrowserError("INVALID_ARGUMENT", f"{key}는 0~200 정수입니다.")
    elements = state["elements"]
    visible = [e for e in elements if e["state"]["visible"]]
    children: dict[str | None, list[str]] = {}
    for element in elements:
        children.setdefault(element.get("parent"), []).append(element["id"])

    def descendant_count(root: str) -> int:
        """부모 우선 순서의 의미 트리에서 root 아래 요소 수를 센다."""
        count, stack = 0, list(children.get(root, []))
        while stack:
            current = stack.pop()
            count += 1
            stack.extend(children.get(current, []))
        return count

    regions = []
    for element in visible:
        role = element["role"]
        if role not in LANDMARKS or (role in NAMED_ONLY and not element.get("name")):
            continue
        regions.append({"id": element["id"], "role": role, "name": (element.get("name") or "")[:80],
                        "elements": descendant_count(element["id"])})
    headings = [{"id": e["id"], "level": heading_level(e), "text": (e.get("name") or e.get("text") or "")[:120]}
                for e in visible if e["role"] == "heading"]
    alerts = [{"id": e["id"], "role": e["role"], "text": (e.get("text") or e.get("name") or "")[:200]}
              for e in visible if e["role"] in {"alert", "status"} and (e.get("text") or e.get("name"))]
    invalid = [{"id": e["id"], "name": (e.get("name") or "")[:80], "message": (e.get("validation_message") or "")[:120]}
               for e in visible if e["state"].get("invalid") and e["role"] in {"textbox", "searchbox", "combobox", "spinbutton", "checkbox", "radio"}]
    roles = Counter(e["role"] for e in visible)
    search_box = next(({"id": e["id"], "name": (e.get("name") or "")[:80]} for e in visible
                       if e["role"] == "searchbox" or (e["role"] == "textbox" and any(
                           word in (e.get("name") or "").casefold() for word in ("검색", "search")))), None)
    page = state["page"]
    error_events = [e for e in events if e["type"] in {"script_error", "network_failed", "not_found", "server_error",
                                                       "authentication_required", "http_error", "browser_crashed"}]
    error_counts = Counter(e["type"] for e in error_events)
    counts = {"total": len(elements), "visible": len(visible), "hidden": len(elements) - len(visible),
              "interactive": sum(1 for e in visible if e["actions"]), "links": roles.get("link", 0),
              "buttons": roles.get("button", 0), "inputs": sum(roles.get(r, 0) for r in ("textbox", "searchbox", "combobox", "checkbox", "radio")),
              "forms": roles.get("form", 0), "images": roles.get("image", 0), "headings": roles.get("heading", 0),
              "tables": roles.get("table", 0), "lists": roles.get("list", 0), "frames": len(state.get("frames", [])),
              "by_role": dict(roles.most_common(12))}
    omitted_total = len(elements)
    return {
        "page": page,
        "counts": counts,
        "regions": regions[:max_regions],
        "regions_omitted": max(0, len(regions) - max_regions),
        "headings": headings[:max_headings],
        "headings_omitted": max(0, len(headings) - max_headings),
        "search_box": search_box,
        "errors": {"http_status": page.get("http_status") if (page.get("http_status") or 0) >= 400 else None,
                   "authentication": page.get("authentication"), "alerts": alerts[:10], "invalid_inputs": invalid[:10],
                   "events": [{"type": e["type"], "url": short_url(e["url"]) if e.get("url") else None, "message": (e.get("message") or "")[:120]}
                              for e in error_events[-3:]],
                   "event_counts": dict(error_counts),
                   "warnings": [{k: v for k, v in w.items() if k != "message"} | ({"message": str(w["message"])[:120]} if w.get("message") else {})
                                for w in state.get("warnings", [])[:5]]},
        "kind": kind or "other",
        "omitted": {
            "elements": omitted_total,
            "reason": "summary 모드는 요소 목록을 포함하지 않습니다.",
            "next": NEXT_BY_KIND.get(kind or "other", NEXT_BY_KIND["other"]),
        },
    }
