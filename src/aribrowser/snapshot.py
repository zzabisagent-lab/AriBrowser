"""전체 관측 상태의 차이 계산과 Agent용 텍스트/부분 화면 표현을 담당한다.

필터링은 차이 계산 뒤에 적용한다. 출력에서 생략한 요소를 실제 DOM에서 삭제된
요소로 오인하지 않도록 원본 스냅샷과 출력용 뷰를 분리하는 것이 핵심이다.

v0.1.0의 출력 형식:
- `format=text`(기본): 들여쓰기된 텍스트 화면만 반환한다. 요소 ID는 JSON과 같다.
- `format=elements`: 요소 목록만 반환한다. 기본은 빈 필드/기본 상태를 생략한 compact 형태다.
- `format=both`: 텍스트 화면과 요소 목록을 함께 반환한다(v0.0.1 호환).
- `format=summary`: 페이지 요약만 반환한다(summary.py).
모든 형식은 offset/limit 페이지네이션과 max_bytes/max_elements/max_tokens 예산을 적용한다.
"""

import copy
import json

from .budget import Limits, compact_element, limit_items, parse_compact, parse_fields, shape_elements
from .errors import BrowserError

MODES = {"full", "viewport", "main", "interactive", "region"}
FORMATS = {"text", "elements", "both", "summary"}


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
            if old[key].get(field) != new[key].get(field) and field != "internal_id"
        }
        if fields:
            changed.append({"id": key, "changes": fields})
    page_changes = {
        key: {"before": before["page"].get(key), "after": value}
        for key, value in after["page"].items()
        if before["page"].get(key) != value and key != "stability"
    }
    return {
        "added": [new[k] for k in new if k not in old],
        "removed": [{"id": k, "role": old[k]["role"]} for k in old if k not in new],
        "changed": sorted(changed, key=lambda item: item["id"]),
        "page": page_changes,
        "navigation": page_changes.get("url"),
    }


def shape_changes(diff: dict, limits: Limits, compact: bool, main_frame: str | None) -> tuple[dict, dict]:
    """변경분의 추가 요소를 compact로 바꾸고 예산을 적용한다. (변경분, 예산 정보)를 돌려준다."""
    added = shape_elements(diff["added"], compact, None, main_frame)
    limited = limit_items(added, limits)
    result = {**diff, "added": limited["items"],
              "omitted": {"added": len(added) - len(limited["items"]), "next_offset": limited["next_offset"]}}
    return result, limited


def descendants(elements: list[dict], root: str) -> set[str]:
    """순서가 부모 우선인 의미 트리에서 root 자신과 하위 요소 ID를 구한다."""
    ids = {root}
    for element in elements:
        if element.get("parent") in ids:
            ids.add(element["id"])
    return ids


def parse_format(options: dict, default: str = "text") -> str:
    """format 매개변수를 검증한다."""
    value = options.get("format", default)
    if value not in FORMATS:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 출력 형식입니다.", format=value, allowed=sorted(FORMATS))
    return value


def select_view(snapshot: dict, options: dict, limits: Limits | None = None, main_frame: str | None = None) -> dict:
    """범위/페이지네이션/예산을 적용하고 생략량과 다음 offset을 명시한다."""
    limits = limits or Limits()
    mode = options.get("mode", "full")
    if mode not in MODES:
        raise BrowserError("INVALID_ARGUMENT", "지원하지 않는 출력 모드입니다.", mode=mode, allowed=sorted(MODES | {"changes", "summary"}))
    output_format = parse_format(options)
    fields = parse_fields(options.get("fields"))
    compact = parse_compact(options)
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
    page_window = elements[offset:offset + limit]
    shaped = shape_elements(page_window, compact, fields, main_frame)
    # 텍스트 형식은 한 줄 길이로, 요소 형식은 JSON 길이로 예산을 계산한다.
    if output_format == "text":
        depth = {}
        limited = limit_items(list(zip(page_window, shaped)), limits,
                              render=lambda pair: text_line(pair[0], depth), offset=offset)
        selected_full = [pair[0] for pair in limited["items"]]
        selected_shaped = [pair[1] for pair in limited["items"]]
    else:
        limited = limit_items(list(zip(page_window, shaped)), limits, render=lambda pair: json.dumps(pair[1], ensure_ascii=False), offset=offset)
        selected_full = [pair[0] for pair in limited["items"]]
        selected_shaped = [pair[1] for pair in limited["items"]]
    remaining = max(0, len(elements) - offset - len(selected_full))
    next_offset = offset + len(selected_full) if remaining else None
    view = {
        "page": copy.deepcopy(snapshot["page"]),
        "frames": snapshot.get("frames", []),
        "warnings": snapshot.get("warnings", []),
        "scope": {"mode": mode, "region": region, "offset": offset, "limit": limit, "format": output_format,
                  "compact": compact if fields is None else None, "fields": fields},
        "omitted": {
            "total": len(snapshot["elements"]) - len(selected_full),
            "filtered": len(snapshot["elements"]) - len(elements),
            "remaining": remaining,
            "next_offset": next_offset,
            "available": len(elements),
            "unmaterialized": "DOM에 아직 생성되지 않은 가상 목록은 scroll 후 다시 조회하세요.",
        },
        "_limited": limited,
    }
    if output_format in {"elements", "both"}:
        view["elements"] = selected_shaped
    if output_format in {"text", "both"}:
        view["text"] = text_screen({"page": view["page"], "elements": selected_full, "omitted": view["omitted"]})
    return view


def text_line(element: dict, depth: dict) -> str:
    """요소 하나를 텍스트 화면 한 줄로 만든다. depth는 부모 깊이 누적용 공유 사전이다."""
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
    suffix = f" = {json.dumps(value, ensure_ascii=False)}" if value not in (None, "") else ""
    if element.get("href") and element["role"] == "link":
        suffix += f" -> {json.dumps(element['href'], ensure_ascii=False)}"
    if element.get("media"):
        suffix += " " + json.dumps({k: v for k, v in element["media"].items() if k in {"alt", "needs_visual", "loaded"} and v}, ensure_ascii=False)
    head = " ".join(part for part in (element["id"], element["role"], " ".join(flags)) if part)
    return f"{'  ' * level}[{head}] {label}{suffix}"


def text_screen(view: dict) -> str:
    """웹 텍스트는 JSON 문자열로 이스케이프하여 가짜 구조 행 삽입을 막는다."""
    lines = ["PAGE", f"  title: {json.dumps(view['page'].get('title', ''), ensure_ascii=False)}",
             f"  url: {json.dumps(view['page'].get('url', ''), ensure_ascii=False)}"]
    depth = {}
    for element in view["elements"]:
        lines.append(text_line(element, depth))
    if view["omitted"]["total"]:
        lines.append("OMITTED " + json.dumps({k: v for k, v in view["omitted"].items() if k != "unmaterialized"}, ensure_ascii=False))
    return "\n".join(lines)


__all__ = ["compare", "descendants", "select_view", "text_screen", "shape_changes", "parse_format", "compact_element"]
