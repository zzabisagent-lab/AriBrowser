"""응답 크기(바이트/요소 수/예상 토큰) 예산과 요소 출력 경량화를 담당한다.

대형 페이지에서 관측 명령 하나가 수십만 토큰의 응답을 만들면 Agent의 컨텍스트와
비용을 통제할 수 없다. 모든 관측 명령은 이 모듈을 통해 다음을 보장한다.

- `max_bytes`, `max_elements`, `max_tokens` 한도에 도달하면 응답을 자르고
  `budget.truncated`와 `budget.next_offset`으로 이어서 조회할 위치를 알린다.
- 실제 바이트 수와 요소 수, 예상 토큰 수, 추정에 사용한 tokenizer 이름을 함께 돌려준다.
- 요소는 기본적으로 빈 필드와 기본 상태값을 생략한 간결한 형태(compact)로 출력하며,
  `fields`로 필요한 필드만 고를 수 있다.

토큰 추정은 외부 tokenizer 의존성 없이 문자 종류별 가중치로 계산하는 근사값이다.
한글·한자·가나는 글자당 약 1토큰, ASCII는 4자당 1토큰, 그 외 문자는 2자당 1토큰으로
본다. 실제 모델의 tokenizer와 수치가 다를 수 있으므로 상한 설정에는 여유를 둔다.
"""

import json
from dataclasses import dataclass

from .errors import BrowserError

TOKENIZER = "heuristic-v1 (cjk=1/char, ascii=1/4chars, other=1/2chars)"

# collector.js가 만드는 전체 요소 필드. fields 선택 검증과 문서화의 기준이다.
ELEMENT_FIELDS = {
    "id", "parent", "frame", "role", "tag", "name", "text", "text_truncated", "value", "labels", "description",
    "validation_message", "href", "input_type", "state", "actions", "groups", "bounds", "style", "media",
    "accessibility_source", "row_index", "column_index", "set_size", "position_in_set", "internal_id",
}

# compact 출력에서 생략하는 상태 기본값. 생략된 키는 이 값으로 해석한다.
STATE_DEFAULTS = {
    "visible": True, "in_viewport": True, "occluded": False, "disabled": False, "readonly": False,
    "selected": False, "checked": False, "expanded": None, "invalid": False, "required": False,
    "focused": False, "accessibility_hidden": False, "actionable": True,
}


def estimate_tokens(text: str) -> int:
    """문자 종류별 가중치로 LLM 토큰 수를 근사한다. 0 이상의 정수를 반환한다."""
    ascii_count = cjk_count = other_count = 0
    for char in text:
        code = ord(char)
        if code < 128:
            ascii_count += 1
        elif 0xAC00 <= code <= 0xD7A3 or 0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x30FF \
                or 0x1100 <= code <= 0x11FF or 0x3130 <= code <= 0x318F or 0x3400 <= code <= 0x4DBF:
            cjk_count += 1
        else:
            other_count += 1
    return int(ascii_count / 4 + cjk_count + other_count / 2 + 0.999)


def serialize(value) -> str:
    """프로토콜 출력과 같은 압축 JSON 직렬화를 사용해 크기 측정이 실제 응답과 일치하게 한다."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def measure(value) -> dict:
    """값의 실제 바이트 수와 예상 토큰 수를 계산한다."""
    text = value if isinstance(value, str) else serialize(value)
    return {"bytes": len(text.encode("utf-8")), "estimated_tokens": estimate_tokens(text)}


@dataclass
class Limits:
    """한 응답에 허용하는 상한. None은 해당 한도를 적용하지 않는다는 뜻이다."""

    max_bytes: int | None = None
    max_elements: int | None = None
    max_tokens: int | None = None

    def as_dict(self) -> dict:
        """응답의 budget.limits에 그대로 싣는다."""
        return {"max_bytes": self.max_bytes, "max_elements": self.max_elements, "max_tokens": self.max_tokens}


def parse_limits(options: dict, default_max_bytes: int | None = None) -> Limits:
    """요청 매개변수에서 예산을 읽고 범위를 검증한다. 요청값이 세션 기본값보다 우선한다."""
    limits = Limits(max_bytes=default_max_bytes or None)
    for key, minimum in (("max_bytes", 256), ("max_elements", 1), ("max_tokens", 16)):
        if key not in options:
            continue
        value = options[key]
        if value is None or value == 0:
            setattr(limits, key, None)
            continue
        if type(value) is not int or value < minimum:
            raise BrowserError("INVALID_ARGUMENT", f"{key}는 {minimum} 이상의 정수 또는 0(무제한)입니다.", **{key: value})
        setattr(limits, key, value)
    return limits


def limit_items(items: list, limits: Limits, render=None, offset: int = 0) -> dict:
    """항목을 순서대로 담되 어느 한도든 넘기 전에 멈추고 잘림 정보와 다음 offset을 돌려준다.

    render는 항목 하나를 실제 출력 문자열로 바꾸는 함수다(JSON 또는 텍스트 한 줄).
    첫 항목만으로 한도를 넘더라도 Agent가 진행할 수 있도록 한 개는 포함하고
    `oversized`로 표시한다.
    """
    render = render or serialize
    selected, used_bytes, used_tokens, reason, oversized = [], 0, 0, None, False
    for item in items:
        if limits.max_elements is not None and len(selected) >= limits.max_elements:
            reason = "max_elements"
            break
        text = render(item)
        size, tokens = len(text.encode("utf-8")), estimate_tokens(text)
        exceeds_bytes = limits.max_bytes is not None and used_bytes + size > limits.max_bytes
        exceeds_tokens = limits.max_tokens is not None and used_tokens + tokens > limits.max_tokens
        if (exceeds_bytes or exceeds_tokens) and selected:
            reason = "max_bytes" if exceeds_bytes else "max_tokens"
            break
        if exceeds_bytes or exceeds_tokens:
            oversized = True
        selected.append(item)
        used_bytes += size
        used_tokens += tokens
    truncated = len(selected) < len(items)
    return {"items": selected, "truncated": truncated, "truncated_by": reason,
            "next_offset": offset + len(selected) if truncated else None,
            "bytes": used_bytes, "estimated_tokens": used_tokens, "oversized": oversized}


def budget_report(result: dict, limits: Limits, elements: int, truncated: bool, truncated_by=None,
                  next_offset=None, oversized: bool = False) -> dict:
    """결과 전체를 측정해 budget 블록을 만든다. 호출자는 이 블록을 결과에 넣는다."""
    measured = measure(result)
    report = {"bytes": measured["bytes"], "elements": elements, "estimated_tokens": measured["estimated_tokens"],
              "tokenizer": TOKENIZER, "limits": limits.as_dict(), "truncated": truncated}
    if truncated:
        report["truncated_by"] = truncated_by
        report["next_offset"] = next_offset
    if oversized:
        report["oversized"] = True
    return report


def parse_fields(value, allowed: set[str] = ELEMENT_FIELDS) -> list[str] | None:
    """fields 매개변수를 검증한다. None이면 기본 필드 집합을 쓴다."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(f, str) for f in value):
        raise BrowserError("INVALID_ARGUMENT", "fields는 문자열 배열입니다.")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise BrowserError("INVALID_ARGUMENT", "알 수 없는 필드입니다.", fields=unknown, allowed=sorted(allowed))
    return value


def expand_element(item: dict) -> dict:
    """collector.js의 압축 항목을 v0.0.1과 같은 전체 스키마로 되돌린다.

    수집기는 전송량을 줄이려고 기본값과 같은 상태, 빈 문자열/배열, 정상 스타일을
    생략하고 bounds를 [x, y, width, height] 정수 배열로 보낸다. Python 내부 로직
    (차이 계산, 검색, 텍스트 화면)은 전체 스키마를 가정하므로 여기서 채운다.
    """
    bounds = item.pop("b", [0, 0, 0, 0])
    state = {**STATE_DEFAULTS, **item.get("state", {})}
    style = item.get("style") or {"emphasis": "normal", "meaning": "normal",
                                  "visibility": "visible" if state["visible"] else "hidden", "position": "flow"}
    return {
        "id": item["id"], "parent": item.get("parent"), "role": item["role"], "tag": item.get("tag", ""),
        "name": item.get("name", ""), "text": item.get("text", item.get("name", "")),
        "text_truncated": bool(item.get("text_truncated", False)), "value": item.get("value"),
        "labels": item.get("labels", []), "description": item.get("description", ""),
        "validation_message": item.get("validation_message", ""), "href": item.get("href"),
        "input_type": item.get("input_type"), "state": state, "actions": item.get("actions", []),
        "groups": item.get("groups", {}),
        "bounds": {"x": bounds[0], "y": bounds[1], "width": bounds[2], "height": bounds[3]},
        "style": style, "media": item.get("media"), "accessibility_source": "dom-aria",
        "row_index": item.get("row_index"), "column_index": item.get("column_index"),
        "set_size": item.get("set_size"), "position_in_set": item.get("position_in_set"),
    }


def compact_state(state: dict) -> dict:
    """기본값과 다른 상태만 남긴다. 비어 있으면 호출자가 state 키 자체를 생략한다."""
    return {key: value for key, value in state.items() if STATE_DEFAULTS.get(key, False) != value}


def compact_element(element: dict, fields: list[str] | None = None, main_frame: str | None = None) -> dict:
    """빈 필드·기본 상태·중복 텍스트·디버깅 정보를 제거한 Agent용 요소 표현을 만든다.

    fields가 주어지면 id와 함께 요청한 필드만 그대로 돌려준다(compact 규칙 미적용).
    그렇지 않으면 아래 규칙을 적용한다.
    - name과 같은 text, 빈 문자열, None, 빈 배열은 생략한다.
    - state는 STATE_DEFAULTS와 다른 값만 남기고, 모두 기본값이면 키를 생략한다.
    - bounds/groups/labels/style/accessibility_source는 fields로 요청할 때만 포함한다.
    - 주 프레임의 frame 값은 생략하고 iframe 요소에만 frame을 남긴다.
    """
    if fields is not None:
        result = {"id": element["id"]}
        for field in fields:
            if field in element:
                result[field] = element[field]
        return result
    result = {"id": element["id"], "role": element["role"]}
    if element.get("parent"):
        result["parent"] = element["parent"]
    if element.get("frame") and element["frame"] != main_frame:
        result["frame"] = element["frame"]
    name, text = element.get("name") or "", element.get("text") or ""
    if name:
        result["name"] = name
    if text and text != name:
        result["text"] = text
    if element.get("text_truncated"):
        result["text_truncated"] = True
    value = element.get("value")
    if value not in (None, ""):
        result["value"] = value
    for key in ("href", "description", "validation_message"):
        if element.get(key):
            result[key] = element[key]
    if element.get("input_type") and element.get("tag") in {"input", "select", "textarea", "button"}:
        result["input_type"] = element["input_type"]
    state = compact_state(element.get("state", {}))
    if state:
        result["state"] = state
    if element.get("actions"):
        result["actions"] = element["actions"]
    media = element.get("media")
    if media:
        result["media"] = {key: media[key] for key in ("alt", "source", "size", "loaded", "needs_visual", "context") if media.get(key)}
    for key in ("row_index", "column_index", "set_size", "position_in_set"):
        if element.get(key) is not None:
            result[key] = element[key]
    return result


def shape_elements(elements: list[dict], compact: bool, fields: list[str] | None, main_frame: str | None = None) -> list[dict]:
    """compact/fields 옵션에 따라 요소 목록을 출력 형태로 바꾼다. 전체 형태는 복사본이다."""
    if fields is None and not compact:
        return [dict(element) for element in elements]
    return [compact_element(element, fields, main_frame) for element in elements]


def parse_compact(options: dict, default: bool = True) -> bool:
    """compact 매개변수를 검증한다."""
    value = options.get("compact", default)
    if not isinstance(value, bool):
        raise BrowserError("INVALID_ARGUMENT", "compact는 true/false입니다.")
    return value
