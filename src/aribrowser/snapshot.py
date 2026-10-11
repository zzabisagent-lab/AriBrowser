"""전체 관측 상태의 차이 계산과 Agent용 텍스트/부분 화면 표현을 담당한다.

필터링은 차이 계산 뒤에 적용한다. 출력에서 생략한 요소를 실제 DOM에서 삭제된
요소로 오인하지 않도록 원본 스냅샷과 출력용 뷰를 분리하는 것이 핵심이다.
"""

import copy
import json

from .errors import BrowserError


def compare(before: dict | None, after: dict) -> dict:
    """동일 ID의 속성 변경과 요소 추가/삭제, 탐색 상태 변화를 반환한다."""
    before = before or {"elements": [], "page": {}}
    old = {e["id"]: e for e in before["elements"]}
    new = {e["id"]: e for e in after["elements"]}
    changed = []
    for key in old.keys() & new.keys():
        fields = {
            field: {"before": old[key].get(field), "after": new[key].get(field)}
            for field in old[key].keys() | new[key].keys()
            if old[key].get(field) != new[key].get(field)
        }
        if fields:
            changed.append({"id": key, "changes": fields})
    page_changes = {
        key: {"before": before["page"].get(key), "after": value}
        for key, value in after["page"].items()
        if before["page"].get(key) != value
    }
    return {
        "added": [new[k] for k in new if k not in old],
        "removed": [{"id": k, "role": old[k]["role"]} for k in old if k not in new],
        "changed": sorted(changed, key=lambda item: item["id"]),
        "page": page_changes,
        "navigation": page_changes.get("url"),
    }


def descendants(elements: list[dict], root: str) -> set[str]:
    """순서가 부모 우선인 의미 트리에서 root 자신과 하위 요소 ID를 구한다."""
    ids = {root}
    for element in elements:
        if element.get("parent") in ids:
            ids.add(element["id"])
    return ids


def select_view(snapshot: dict, options: dict) -> dict:
    """범위/페이지네이션을 적용하고 생략량과 다음 offset을 명시한다."""
    mode = options.get("mode", "full")
    if mode not in {"full", "viewport", "main", "interactive", "region"}:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 출력 모드입니다.", mode=mode)
    elements = snapshot["elements"]
    region = options.get("region")
    if mode == "region" and not region:
        raise BrowserError("INVALID_ARGUMENT", "region 모드에는 region ID가 필요합니다.")
    if region:
        if not any(e["id"] == region for e in elements):
            raise BrowserError("ELEMENT_NOT_FOUND", "영역을 찾지 못했습니다.", element=region)
        scope = descendants(elements, region)
        elements = [e for e in elements if e["id"] in scope]
    if mode == "main":
        roots = [e["id"] for e in elements if e["role"] == "main"]
        scope = set().union(*(descendants(elements, r) for r in roots)) if roots else set()
        elements = [e for e in elements if e["id"] in scope] if roots else elements
    if not options.get("include_hidden", False):
        elements = [e for e in elements if e["state"]["visible"]]
    if mode == "viewport":
        elements = [e for e in elements if e["state"]["in_viewport"]]
    if mode == "interactive":
        elements = [e for e in elements if e["actions"]]
    offset = options.get("offset", 0)
    limit = options.get("limit", 500)
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 10000:
        raise BrowserError("INVALID_ARGUMENT", "offset >= 0, 1 <= limit <= 10000이어야 합니다.")
    selected = elements[offset:offset + limit]
    return {
        "page": copy.deepcopy(snapshot["page"]),
        "elements": copy.deepcopy(selected),
        "frames": snapshot.get("frames", []),
        "warnings": snapshot.get("warnings", []),
        "scope": {"mode": mode, "region": region, "offset": offset, "limit": limit},
        "omitted": {
            "total": len(snapshot["elements"]) - len(selected),
            "filtered": len(snapshot["elements"]) - len(elements),
            "remaining": max(0, len(elements) - offset - len(selected)),
            "next_offset": offset + limit if offset + limit < len(elements) else None,
            "available": len(elements),
            "unmaterialized": "DOM에 아직 생성되지 않은 가상 목록은 scroll 후 다시 조회하세요.",
        },
    }


def text_screen(view: dict) -> str:
    """웹 텍스트는 JSON 문자열로 이스케이프하여 가짜 구조 행 삽입을 막는다."""
    lines = ["PAGE", f"  title: {json.dumps(view['page'].get('title', ''), ensure_ascii=False)}",
             f"  url: {json.dumps(view['page'].get('url', ''), ensure_ascii=False)}"]
    depth = {}
    for element in view["elements"]:
        level = min(depth.get(element.get("parent"), 0) + 1, 12)
        depth[element["id"]] = level
        flags = [k for k in ("selected", "checked", "disabled", "readonly", "expanded", "invalid", "occluded")
                 if element["state"].get(k)]
        if not element["state"]["visible"]:
            flags.append("hidden")
        elif not element["state"]["in_viewport"]:
            flags.append("offscreen")
        label = json.dumps(element.get("name") or element.get("text", ""), ensure_ascii=False)
        value = element.get("value")
        suffix = f" = {json.dumps(value, ensure_ascii=False)}" if value is not None else ""
        if element.get("media"):
            suffix += " " + json.dumps(element["media"], ensure_ascii=False)
        lines.append(f"{'  ' * level}[{element['id']} {element['role']} {' '.join(flags)}] {label}{suffix}")
    if view["omitted"]["total"]:
        lines.append("OMITTED " + json.dumps(view["omitted"], ensure_ascii=False))
    return "\n".join(lines)
