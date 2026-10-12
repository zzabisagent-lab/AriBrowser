"""DOM 관측, Chromium 접근성 보강, 안정적인 요소 조회와 차이 반환을 구현한다.

v0.1.0에서 이 모듈은 다음을 추가로 맡는다.
- 수집기가 만든 긴 내부 ID를 탭별 짧은 ID(`t1e12`)로 바꾸고 양방향 매핑을 유지한다.
- `snapshot`의 summary/text/elements/both 형식, 필드 선택, 예산 제한을 적용한다.
- `find`의 정확/정규식/AND-OR 검색, 정렬, 중복 제거, 부모/자식 문맥을 제공한다.
- 브라우저 안에서 실행되는 CSS 선택자 질의(`query`)와 반복 레코드 추출(`extract`),
  정렬·필터·페이지 상태 감지(`page.state`)를 제공한다.
"""

import asyncio
import copy
import json
import re
import time
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from .budget import (
    budget_report,
    compact_element,
    expand_element,
    limit_items,
    parse_compact,
    parse_fields,
    parse_limits,
    shape_elements,
)
from .engine import Engine, Tab
from .errors import BrowserError
from .ids import INTERNAL_ID
from .snapshot import compare, descendants, select_view, shape_changes
from .summary import page_summary

_HERE = Path(__file__).parent
REGISTRY = _HERE.joinpath("registry.js").read_text(encoding="utf-8").strip()


def _script(name: str) -> str:
    """스크립트 파일을 읽고 공용 레지스트리 코드를 삽입한다."""
    return _HERE.joinpath(name).read_text(encoding="utf-8").replace("/*REGISTRY*/", REGISTRY)


COLLECTOR = _script("collector.js")
QUERY = _script("query.js")
PAGESTATE = _script("pagestate.js")
DOM_QUIET = "() => { const a = window.__aribrowser_activity__; return a ? Math.round(performance.now() - a.lastMutation) : null; }"

MATCH_MODES = {"contains", "exact", "regex", "prefix"}
SEARCH_FIELDS = {"name", "text", "value", "href", "description"}
SORT_KEYS = {"document", "name", "role", "position"}
QUERY_FIELDS = {"id", "tag", "text", "name", "role", "href", "src", "value", "visible", "classes", "bounds", "price", "number", "attributes"}
ATTRIBUTE_NAME = re.compile(r"^[A-Za-z_:][-A-Za-z0-9_:.]{0,99}$")
RECORD_TYPES = {"text", "number", "price", "url"}


def _validate_selector(value, name: str = "selector") -> str:
    """CSS 선택자 인자는 길이 제한이 있는 문자열이어야 한다. 문법 검증은 브라우저가 한다."""
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise BrowserError("INVALID_ARGUMENT", f"{name}는 1~1000자의 CSS 선택자 문자열입니다.")
    return value


def _validate_pagination(options: dict, default_limit: int, max_limit: int) -> tuple[int, int]:
    """offset/limit 범위를 검증한다."""
    offset, limit = options.get("offset", 0), options.get("limit", default_limit)
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= max_limit:
        raise BrowserError("INVALID_ARGUMENT", f"offset >= 0, 1 <= limit <= {max_limit}이어야 합니다.")
    return offset, limit


class Browser(Engine):
    """엔진 상태 위에 Agent가 읽는 의미 화면과 요소 조작용 참조를 제공한다."""

    # ------------------------------------------------------------------ ID 관리
    def frame_prefix(self, tab: Tab, frame) -> str:
        """프레임마다 안정적인 접두사를 배정한다. 내부 ID와 레지스트리 키의 일부가 된다."""
        if frame not in tab.frames:
            tab.frames[frame] = f"f{len(tab.frames) + 1}-{id(frame):x}"
        return f"{tab.id}-{tab.frames[frame]}"

    def register(self, tab: Tab, frame, internal_id: str | None) -> str | None:
        """내부 ID를 짧은 ID로 바꾸고 조작 가능하도록 프레임과 연결한다."""
        if not internal_id:
            return None
        short = tab.ids.short(internal_id)
        tab.elements[short] = frame
        return short

    def translate_ids(self, tab: Tab, frame, value):
        """중첩 결과 안의 내부 ID 문자열을 모두 짧은 ID로 바꾼다(page.state/extract 결과용)."""
        if isinstance(value, str):
            return self.register(tab, frame, value) if INTERNAL_ID.match(value) and value.startswith(tab.id + "-") else value
        if isinstance(value, list):
            return [self.translate_ids(tab, frame, item) for item in value]
        if isinstance(value, dict):
            return {key: self.translate_ids(tab, frame, item) for key, item in value.items()}
        return value

    # ------------------------------------------------------------------ 관측
    async def observe(self, tab: Tab) -> dict:
        """각 프레임을 독립적으로 읽어 교차 출처 iframe과 open Shadow DOM을 합친다.

        반환하는 요소의 id/parent/groups는 모두 짧은 ID이며 internal_id에 긴 ID를 둔다.
        page.stability는 SPA 로딩 완료 판단에 쓰는 네트워크/DOM 안정화 지표다.
        """
        elements, frames, warnings = [], [], []
        tab.elements = {}
        current_frames = tab.page.frames
        for removed in set(tab.frames) - set(current_frames):
            del tab.frames[removed]
        live_documents = set()
        dom_quiet = None
        for frame in current_frames:
            prefix = self.frame_prefix(tab, frame)
            try:
                raw = await asyncio.wait_for(frame.evaluate(COLLECTOR, {"prefix": prefix}),
                                             self.config.timeout_ms / 1000)
                data = json.loads(raw)
                data["elements"] = [expand_element(item) for item in data["elements"]]
                parent_internal, offset_x, offset_y = None, 0, 0
                frame_visible = True
                if frame != tab.page.main_frame:
                    host = await frame.frame_element()
                    try:
                        parent_internal = await host.evaluate("el => window.__aribrowser_v001__?.nodeIds.get(el) || null")
                        box = await host.bounding_box()
                        frame_visible = box is not None
                        if box:
                            # iframe 내부 원점은 border 안쪽이므로 테두리 폭을 더한다.
                            border = await host.evaluate("el => ({x: el.clientLeft, y: el.clientTop})")
                            offset_x, offset_y = box["x"] + border["x"], box["y"] + border["y"]
                    finally:
                        await host.dispose()
                else:
                    tab.main_frame_id = prefix
                    try:
                        dom_quiet = await frame.evaluate(DOM_QUIET)
                    except PlaywrightError:
                        dom_quiet = None
                live_documents.add(data["document_id"])
                parent_short = tab.ids.short(parent_internal) if parent_internal else None
                frames.append({"id": prefix, "url": frame.url, "parent_element": parent_short,
                               "document_id": data["document_id"], "viewport": data["viewport"]})
                if data["truncated"]:
                    warnings.append({"code": "DOM_LIMIT", "frame": prefix, "visited": data["visited"], "elements": len(data["elements"]),
                                     "hint": "query/extract로 필요한 영역만 조회하거나 region으로 범위를 좁히세요."})
                if data.get("hidden_truncated"):
                    warnings.append({"code": "HIDDEN_LIMIT", "frame": prefix, "hidden_visited": data["hidden_visited"],
                                     "hint": "숨겨진 서브트리 일부를 생략했습니다. query(include_hidden=true)로 조회할 수 있습니다."})
                viewport = tab.page.viewport_size or {"width": self.config.width, "height": self.config.height}
                for item in data["elements"]:
                    item["frame"] = prefix
                    item["bounds"]["x"] += offset_x
                    item["bounds"]["y"] += offset_y
                    box = item["bounds"]
                    item["state"]["visible"] &= frame_visible
                    item["state"]["in_viewport"] &= frame_visible and box["x"] < viewport["width"] and box["y"] < viewport["height"] and box["x"] + box["width"] > 0 and box["y"] + box["height"] > 0
                await self._accessibility(tab, frame, data["elements"], warnings)
                # AX 보강은 내부 ID 기준으로 끝났으므로 이제 짧은 ID로 바꾼다.
                for item in data["elements"]:
                    item["internal_id"] = item["id"]
                    item["id"] = tab.ids.short(item["id"])
                    item["parent"] = tab.ids.short(item["parent"]) if item["parent"] else parent_short
                    item["groups"] = {key: tab.ids.short(value) for key, value in item["groups"].items()}
                    tab.elements[item["id"]] = frame
                    elements.append(item)
            except (PlaywrightError, asyncio.TimeoutError) as error:
                warnings.append({"code": "FRAME_UNAVAILABLE", "frame": prefix,
                                 "message": str(error) or f"관측 제한 시간({self.config.timeout_ms}ms)을 넘겼습니다. 페이지 스크립트가 바쁘거나 DOM이 매우 큽니다."})
        if not frames:
            raise BrowserError("PAGE_UNRESPONSIVE", "페이지를 관측하지 못했습니다.", warnings=warnings)
        tab.ids.prune(live_documents)
        return {"page": {"tab": tab.id, "profile": tab.profile, "url": tab.page.url,
                         "title": await tab.page.title(), "loading": tab.loading, "http_status": tab.status,
                         "viewport": tab.page.viewport_size,
                         "authentication": "required" if tab.status in {401, 403} else
                         "login_form_detected" if any(e.get("input_type") == "password" for e in elements) else "unknown",
                         "stability": {"network_inflight": len(tab.inflight),
                                       "network_quiet_ms": int((time.monotonic() - tab.last_network) * 1000),
                                       "dom_quiet_ms": dom_quiet}},
                "elements": elements, "frames": frames, "warnings": warnings}

    async def _accessibility(self, tab: Tab, frame, elements: list[dict], warnings: list) -> None:
        """Chromium AX의 실제 계산 이름/역할을 DOM ID에 연결해 ARIA 추측을 보강한다.

        backendDOMNodeId로 실제 노드를 해석하므로 동일 텍스트가 여러 번 나오는
        페이지에서도 이름 기반 오매칭이 없다. CDP 원격 참조는 묶어서 해제한다.
        """
        session = None
        try:
            session = await self.contexts[tab.profile].new_cdp_session(frame)
            await session.send("Accessibility.enable")
            tree = await session.send("Accessibility.getFullAXTree")
            by_id = {e["id"]: e for e in elements}
            nodes = [n for n in tree["nodes"] if not n.get("ignored") and n.get("backendDOMNodeId")
                     and n.get("role", {}).get("value") not in {"StaticText", "InlineTextBox", "RootWebArea", "none"}]
            semaphore = asyncio.Semaphore(16)

            async def enrich(node):
                """원격 AX 노드 하나를 해당 문서의 WeakMap ID와 연결한다."""
                async with semaphore:
                    try:
                        remote = await session.send("DOM.resolveNode", {"backendNodeId": node["backendDOMNodeId"],
                                                                       "objectGroup": "ari-ax"})
                        result = await session.send("Runtime.callFunctionOn", {
                            "objectId": remote["object"]["objectId"],
                            "functionDeclaration": "function(){return this.ownerDocument?.defaultView?.__aribrowser_v001__?.nodeIds.get(this)}",
                            "returnByValue": True,
                        })
                        item = by_id.get(result.get("result", {}).get("value"))
                        if not item:
                            return
                        role = node.get("role", {}).get("value", item["role"])
                        # generic/LabelText는 텍스트 의미를 떨어뜨리므로 수집기의 역할을 유지한다.
                        if role not in {"generic", "LabelText", "section", "none"}:
                            item["role"] = {"img": "image", "Iframe": "iframe"}.get(role, role)
                        if node.get("name", {}).get("value"):
                            item["name"] = str(node["name"]["value"])[:4000]
                        item["description"] = node.get("description", {}).get("value", item["description"])
                        item["accessibility_source"] = "chromium-ax"
                        for prop in node.get("properties", []):
                            key, value = prop["name"], prop.get("value", {}).get("value")
                            if key in {"disabled", "readonly", "required", "expanded", "selected", "checked", "invalid"}:
                                converted = {"true": True, "false": False}.get(str(value).lower(), value)
                                # AX는 사용자 입력 전 HTML 유효성 오류를 아직 노출하지 않을 수 있다.
                                item["state"][key] = item["state"].get(key) or converted if key in {"invalid", "disabled"} else converted
                    except PlaywrightError:
                        pass  # 순회 중 제거된 AX 노드는 다음 관측에서 정확한 삭제로 반영한다.

            await asyncio.gather(*(enrich(n) for n in nodes[:5000]))
            if len(nodes) > 5000:
                warnings.append({"code": "AX_LIMIT", "total": len(nodes)})
        except PlaywrightError as error:
            warnings.append({"code": "AX_UNAVAILABLE", "message": str(error)})
        finally:
            if session:
                try:
                    await session.send("Runtime.releaseObjectGroup", {"objectGroup": "ari-ax"})
                    await session.detach()
                except PlaywrightError:
                    pass  # 프레임 탐색/종료 시 Chromium이 해당 원격 객체를 이미 해제한다.

    # ------------------------------------------------------------------ 화면
    async def snapshot(self, tab_id: str | None = None, **options) -> dict:
        """전체 상태를 기준선으로 저장하되 응답은 요청한 요약/전체/부분/변경분만 제공한다."""
        tab = self.get_tab(tab_id)
        state = await self.observe(tab)
        diff = compare(tab.previous, state)
        event_data = self.event_view(tab.event_cursor)
        # 옵션 검증 실패가 기존 기준선을 소비하지 않도록 뷰를 먼저 생성한다.
        mode = options.get("mode", "full")
        limits = parse_limits(options, self.config.max_bytes)
        if mode == "changes":
            changes, limited = shape_changes(diff, limits, parse_compact(options), tab.main_frame_id)
            result = {"changes": changes, **event_data, "page": state["page"], "warnings": state["warnings"]}
            result["budget"] = budget_report(result, limits, len(changes["added"]), limited["truncated"],
                                             limited["truncated_by"], limited["next_offset"], limited["oversized"])
        elif mode == "summary":
            result = {**page_summary(state, event_data["events"], options), **event_data}
            result["budget"] = budget_report(result, limits, 0, False)
        else:
            view = select_view(state, options, limits, tab.main_frame_id)
            limited = view.pop("_limited")
            result = {**view, **event_data}
            result["budget"] = budget_report(result, limits, len(limited["items"]), limited["truncated"],
                                             limited["truncated_by"], limited["next_offset"], limited["oversized"])
        tab.previous = copy.deepcopy(state)
        tab.event_cursor = event_data["cursor"]
        return result

    async def element_handle(self, tab: Tab, element_id: str):
        """선택 당시의 실제 DOM 노드만 반환하며 이름이 같은 새 요소로 재탐색하지 않는다.

        짧은 ID와 이전 형식의 긴 내부 ID를 모두 받는다.
        """
        internal = tab.ids.resolve(element_id)
        short = tab.ids.short(internal) if internal else None
        frame = tab.elements.get(short) if short else None
        if frame is None:
            code = "STALE_ELEMENT" if tab.ids.is_known_format(element_id) else "ELEMENT_NOT_FOUND"
            raise BrowserError(code, "현재 문서에 해당 요소 ID가 없습니다.", element=element_id)
        try:
            handle = await frame.evaluate_handle("id => window.__aribrowser_v001__?.get(id) || null", internal)
            element = handle.as_element()
            if element is None:
                await handle.dispose()
                raise BrowserError("STALE_ELEMENT", "요소가 제거되었거나 문서가 바뀌었습니다.", element=element_id)
            return element
        except PlaywrightError as error:
            raise BrowserError("STALE_ELEMENT", str(error), element=element_id) from error

    # ------------------------------------------------------------------ 검색
    @staticmethod
    def _matcher(needle: str, mode: str):
        """검색 모드별 비교 함수를 만든다. 정규식 오류는 INVALID_ARGUMENT다."""
        if mode == "regex":
            if len(needle) > 500:
                raise BrowserError("INVALID_ARGUMENT", "정규식은 500자 이하입니다.")
            try:
                pattern = re.compile(needle, re.IGNORECASE)
            except re.error as error:
                raise BrowserError("INVALID_ARGUMENT", "정규식을 해석하지 못했습니다.", pattern=needle, reason=str(error)) from error
            return lambda value: bool(pattern.search(value))
        folded = needle.casefold()
        if mode == "exact":
            return lambda value: value.casefold() == folded
        if mode == "prefix":
            return lambda value: value.casefold().startswith(folded)
        return lambda value: folded in value.casefold()

    @staticmethod
    def _brief(element: dict) -> dict:
        """문맥(조상/자식)용 최소 표현. 이름이 없으면 텍스트를 쓴다."""
        brief = {"id": element["id"], "role": element["role"]}
        label = element.get("name") or element.get("text") or ""
        if label:
            brief["name"] = label[:200]
        for key in ("href", "value"):
            if element.get(key) not in (None, ""):
                brief[key] = element[key]
        return brief

    def _children_tree(self, kids: dict, root: str, depth: int, roles: set[str] | None, flat: list | None = None, level: int = 1) -> list:
        """지정 깊이까지 자식을 모은다. roles가 있으면 해당 역할만 평면 목록으로 돌려준다."""
        result = []
        for child in kids.get(root, [])[:50]:
            if roles is None:
                node = self._brief(child)
                if depth > level:
                    nested = self._children_tree(kids, child["id"], depth, None, None, level + 1)
                    if nested:
                        node["children"] = nested
                result.append(node)
            else:
                if child["role"] in roles:
                    flat.append({**self._brief(child), "depth": level})
                if depth > level:
                    self._children_tree(kids, child["id"], depth, roles, flat, level + 1)
        return result if roles is None else flat

    async def find(self, tab_id: str | None = None, **options) -> dict:
        """텍스트/역할/영역/조작 가능 여부로 검색하며 정확·정규식·AND/OR, 정렬, 중복 제거, 문맥을 지원한다."""
        tab = self.get_tab(tab_id)
        state = await self.observe(tab)
        elements = state["elements"]
        if options.get("region"):
            if not any(e["id"] == options["region"] for e in elements):
                raise BrowserError("ELEMENT_NOT_FOUND", "영역을 찾지 못했습니다.")
            scope = descendants(elements, options["region"])
            elements = [e for e in elements if e["id"] in scope]
        if not options.get("include_hidden", True):
            elements = [e for e in elements if e["state"]["visible"]]
        mode = options.get("match", "contains")
        if mode not in MATCH_MODES:
            raise BrowserError("INVALID_ARGUMENT", "match는 contains/exact/regex/prefix입니다.", match=mode)
        operator = options.get("operator", "and")
        if operator not in {"and", "or"}:
            raise BrowserError("INVALID_ARGUMENT", "operator는 and 또는 or입니다.")
        search_in = options.get("in", ["name", "text", "value"])
        if not isinstance(search_in, list) or not search_in or set(search_in) - SEARCH_FIELDS:
            raise BrowserError("INVALID_ARGUMENT", "in은 name/text/value/href/description 배열입니다.")
        needles = options.get("text", "")
        needles = [needles] if isinstance(needles, str) else needles
        if not isinstance(needles, list) or not all(isinstance(n, str) for n in needles):
            raise BrowserError("INVALID_ARGUMENT", "text는 문자열 또는 문자열 배열입니다.")
        matchers = [self._matcher(n, mode) for n in needles if n != ""]
        roles = options.get("role")
        roles = [roles] if isinstance(roles, str) else roles
        matches = []
        for element in elements:
            if matchers:
                values = [str(element.get(f) or "") for f in search_in]
                hits = [any(m(v) for v in values) for m in matchers]
                if not (all(hits) if operator == "and" else any(hits)):
                    continue
            if roles and element["role"] not in roles:
                continue
            if options.get("actionable") and not (element["actions"] and element["state"]["actionable"]):
                continue
            matches.append(element)
        sort_by = options.get("sort_by", "document")
        if sort_by not in SORT_KEYS:
            raise BrowserError("INVALID_ARGUMENT", "sort_by는 document/name/role/position입니다.")
        if sort_by == "name":
            matches.sort(key=lambda e: (e.get("name") or e.get("text") or "").casefold())
        elif sort_by == "role":
            matches.sort(key=lambda e: e["role"])
        elif sort_by == "position":
            matches.sort(key=lambda e: (e["bounds"]["y"], e["bounds"]["x"]))
        dedupe = options.get("dedupe", False)
        duplicates = 0
        if dedupe:
            keys = ["role", "name", "href"] if dedupe is True else dedupe
            if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                raise BrowserError("INVALID_ARGUMENT", "dedupe는 true 또는 필드 이름 배열입니다.")
            seen, unique = set(), []
            for element in matches:
                key = tuple(str(element.get(k)) for k in keys)
                if key in seen:
                    duplicates += 1
                    continue
                seen.add(key)
                unique.append(element)
            matches = unique
        offset, limit = _validate_pagination(options, 100, 10000)
        with_parent, with_children = options.get("with_parent", 0), options.get("with_children", 0)
        if type(with_parent) is not int or not 0 <= with_parent <= 10 or type(with_children) is not int or not 0 <= with_children <= 5:
            raise BrowserError("INVALID_ARGUMENT", "with_parent는 0~10, with_children은 0~5 정수입니다.")
        child_roles = options.get("child_roles")
        if child_roles is not None and (not isinstance(child_roles, list) or not all(isinstance(r, str) for r in child_roles)):
            raise BrowserError("INVALID_ARGUMENT", "child_roles는 역할 이름 배열입니다.")
        fields = parse_fields(options.get("fields"))
        compact = parse_compact(options)
        by_id = {e["id"]: e for e in state["elements"]}
        kids: dict[str, list] = {}
        for element in state["elements"]:
            kids.setdefault(element.get("parent"), []).append(element)
        window = matches[offset:offset + limit]
        shaped = shape_elements(window, compact, fields, tab.main_frame_id)
        for element, output in zip(window, shaped):
            if with_parent:
                ancestors, parent = [], element.get("parent")
                while parent in by_id and len(ancestors) < with_parent:
                    ancestors.append(self._brief(by_id[parent]))
                    parent = by_id[parent].get("parent")
                output["ancestors"] = ancestors
            if with_children:
                output["children"] = self._children_tree(kids, element["id"], with_children, set(child_roles) if child_roles is not None else None, [] if child_roles is not None else None)
                if len(kids.get(element["id"], [])) > 50:
                    output["children_omitted"] = len(kids[element["id"]]) - 50
        limits = parse_limits(options, self.config.max_bytes)
        limited = limit_items(shaped, limits, offset=offset)
        selected = limited["items"]
        remaining = max(0, len(matches) - offset - len(selected))
        result = {"elements": selected, "total": len(matches), "duplicates_removed": duplicates,
                  "next_offset": offset + len(selected) if remaining else None, "omitted": remaining,
                  "warnings": state["warnings"]}
        result["budget"] = budget_report(result, limits, len(selected), limited["truncated"], limited["truncated_by"],
                                         limited["next_offset"], limited["oversized"])
        return result

    async def detail(self, element_id: str, tab_id: str | None = None, radius: int = 2, **options) -> dict:
        """요소 상세(긴 내부 ID 포함), 의미상 조상, 앞뒤 문맥, 직계 자식을 함께 반환한다."""
        if type(radius) is not int or not 0 <= radius <= 100:
            raise BrowserError("INVALID_ARGUMENT", "radius는 0~100 정수입니다.")
        tab = self.get_tab(tab_id)
        state = await self.observe(tab)
        by_id = {e["id"]: e for e in state["elements"]}
        internal = tab.ids.resolve(element_id)
        short = tab.ids.short(internal) if internal else element_id
        if short not in by_id:
            raise BrowserError("STALE_ELEMENT", "요소가 현재 문서에 없습니다.", element=element_id)
        element = by_id[short]
        fields = parse_fields(options.get("fields"))
        compact = parse_compact(options, default=False)
        ancestors, parent = [], element.get("parent")
        while parent in by_id:
            ancestors.append(self._brief(by_id[parent]))
            parent = by_id[parent].get("parent")
        index = state["elements"].index(element)
        context = state["elements"][max(0, index - radius):index + radius + 1]
        children = [e for e in state["elements"] if e.get("parent") == short]
        return {"element": compact_element(element, fields, tab.main_frame_id) if (compact or fields) else dict(element),
                "ancestors": ancestors,
                "context": [self._brief(e) for e in context],
                "children": shape_elements(children, True, fields, tab.main_frame_id)}

    async def table(self, element_id: str, tab_id: str | None = None, rows=None, columns=None) -> dict:
        """표 ID 기준으로 0부터 시작하는 행/열 인덱스만 선택하고 생략 수를 반환한다."""
        tab = self.get_tab(tab_id)
        state = await self.observe(tab)
        root = next((e for e in state["elements"] if e["id"] == element_id), None)
        if not root or root["role"] not in {"table", "grid", "treegrid"}:
            raise BrowserError("ELEMENT_NOT_FOUND", "표 요소를 찾지 못했습니다.", element=element_id)
        for indices in (rows, columns):
            if indices is not None and (not isinstance(indices, list) or any(type(i) is not int or i < 0 for i in indices)):
                raise BrowserError("INVALID_ARGUMENT", "행/열은 0 이상의 정수 배열입니다.")
        scope = descendants(state["elements"], element_id)
        all_rows = [e for e in state["elements"] if e["id"] in scope and e["role"] == "row"]
        result, skipped_cells = [], 0
        for index, row in enumerate(all_rows):
            if rows is not None and index not in rows:
                continue
            children = descendants(state["elements"], row["id"])
            cells = [e for e in state["elements"] if e["id"] in children and e["role"] in {"cell", "gridcell", "columnheader", "rowheader"}]
            selected = [e for i, e in enumerate(cells) if columns is None or i in columns]
            skipped_cells += len(cells) - len(selected)
            result.append({"id": row["id"], "index": index, "cells": shape_elements(selected, True, None, tab.main_frame_id)})
        return {"id": element_id, "rows": result, "total_rows": len(all_rows),
                "omitted_rows": len(all_rows) - len(result), "omitted_cells": skipped_cells}

    # ------------------------------------------------------------------ 브라우저 내부 질의
    def _resolve_frame(self, tab: Tab, frame_id: str | None):
        """frame 매개변수(프레임 ID)를 Playwright 프레임으로 바꾼다. 생략하면 주 프레임이다."""
        if not frame_id:
            return tab.page.main_frame
        for frame, key in tab.frames.items():
            if f"{tab.id}-{key}" == frame_id and frame in tab.page.frames:
                return frame
        raise BrowserError("ELEMENT_NOT_FOUND", "프레임을 찾지 못했습니다.", frame=frame_id)

    async def _run_query(self, tab: Tab, frame, params: dict) -> dict:
        """query.js를 실행하고 선택자/범위 오류를 프로토콜 오류로 바꾼다."""
        params["prefix"] = self.frame_prefix(tab, frame)
        try:
            data = await asyncio.wait_for(frame.evaluate(QUERY, params), self.config.timeout_ms / 1000)
        except asyncio.TimeoutError as error:
            raise BrowserError("PAGE_UNRESPONSIVE", "브라우저 내부 질의가 제한 시간을 넘겼습니다.") from error
        if data.get("error") == "INVALID_SELECTOR":
            raise BrowserError("INVALID_ARGUMENT", "CSS 선택자를 해석하지 못했습니다.", selector=data.get("selector"), reason=data.get("message"))
        if data.get("error") == "INVALID_REGEX":
            raise BrowserError("INVALID_ARGUMENT", "정규식을 해석하지 못했습니다.", reason=data.get("message"), field=data.get("field"))
        if data.get("error") == "SCOPE_NOT_FOUND":
            raise BrowserError("ELEMENT_NOT_FOUND", "범위(scope/container)를 찾지 못했습니다.", scope=data.get("scope"))
        return data

    def _scope_params(self, tab: Tab, value) -> dict:
        """scope/container 값이 요소 ID면 내부 ID로, 아니면 CSS 선택자로 전달한다."""
        if value is None:
            return {}
        internal = tab.ids.resolve(value)
        if internal:
            return {"scope": internal}
        return {"scopeSelector": _validate_selector(value, "scope")}

    async def query(self, tab_id: str | None = None, **options) -> dict:
        """CSS 선택자로 요소를 브라우저 안에서 선별하고 요청한 필드만 반환한다."""
        tab = self.get_tab(tab_id)
        frame = self._resolve_frame(tab, options.get("frame"))
        selector = _validate_selector(options.get("selector"))
        fields = options.get("fields")
        if fields is not None and (not isinstance(fields, list) or set(fields) - QUERY_FIELDS):
            raise BrowserError("INVALID_ARGUMENT", "알 수 없는 query 필드입니다.", allowed=sorted(QUERY_FIELDS))
        attributes = options.get("attributes", [])
        if not isinstance(attributes, list) or not all(isinstance(a, str) and ATTRIBUTE_NAME.match(a) for a in attributes):
            raise BrowserError("INVALID_ARGUMENT", "attributes는 HTML 속성 이름 배열입니다.")
        if options.get("match", "contains") not in MATCH_MODES:
            raise BrowserError("INVALID_ARGUMENT", "match는 contains/exact/regex/prefix입니다.")
        offset, limit = _validate_pagination(options, 100, 2000)
        dedupe = options.get("dedupe", False)
        if dedupe not in (True, False) and not (isinstance(dedupe, list) and all(isinstance(k, str) for k in dedupe)):
            raise BrowserError("INVALID_ARGUMENT", "dedupe는 true 또는 필드 이름 배열입니다.")
        params = {"mode": "query", "selector": selector, "fields": fields, "attributes": attributes,
                  "text": options.get("text"), "match": options.get("match", "contains"),
                  "includeHidden": bool(options.get("include_hidden", False)), "offset": offset, "limit": limit,
                  "dedupe": dedupe, "maxText": options.get("max_text", 500), **self._scope_params(tab, options.get("scope"))}
        if type(params["maxText"]) is not int or not 1 <= params["maxText"] <= 20000:
            raise BrowserError("INVALID_ARGUMENT", "max_text는 1~20000 정수입니다.")
        data = await self._run_query(tab, frame, params)
        for item in data["items"]:
            if "id" in item:
                item["id"] = self.register(tab, frame, item["id"])
        limits = parse_limits(options, self.config.max_bytes)
        limited = limit_items(data["items"], limits, offset=offset)
        selected = limited["items"]
        result = {"items": selected, "total": data["total"], "matched": data["matched"],
                  "duplicates_removed": data["duplicates_removed"], "offset": offset,
                  "next_offset": limited["next_offset"] if limited["truncated"] else data["next_offset"],
                  "omitted": max(0, data["matched"] - offset - len(selected)), "frame": params["prefix"]}
        result["budget"] = budget_report(result, limits, len(selected), limited["truncated"], limited["truncated_by"],
                                         limited["next_offset"], limited["oversized"])
        return result

    async def extract(self, tab_id: str | None = None, **options) -> dict:
        """반복 구조(상품 카드·게시글·검색 결과)를 레코드 단위로 묶어 추출한다."""
        tab = self.get_tab(tab_id)
        frame = self._resolve_frame(tab, options.get("frame"))
        item = options.get("item")
        if item is not None:
            _validate_selector(item, "item")
        fields = options.get("fields")
        if fields is not None:
            if not isinstance(fields, dict) or not fields or len(fields) > 50:
                raise BrowserError("INVALID_ARGUMENT", "fields는 {이름: 선택자 또는 명세} 객체입니다.")
            for name, spec in fields.items():
                if isinstance(spec, str):
                    _validate_selector(spec, f"fields.{name}")
                    continue
                if not isinstance(spec, dict) or set(spec) - {"selector", "attr", "type", "all", "regex"}:
                    raise BrowserError("INVALID_ARGUMENT", f"fields.{name}은 selector/attr/type/all/regex 명세입니다.")
                if "selector" in spec:
                    _validate_selector(spec["selector"], f"fields.{name}.selector")
                if "attr" in spec and (not isinstance(spec["attr"], str) or not ATTRIBUTE_NAME.match(spec["attr"])):
                    raise BrowserError("INVALID_ARGUMENT", f"fields.{name}.attr은 HTML 속성 이름입니다.")
                if "type" in spec and spec["type"] not in RECORD_TYPES:
                    raise BrowserError("INVALID_ARGUMENT", f"fields.{name}.type은 text/number/price/url입니다.")
                if "all" in spec and not isinstance(spec["all"], bool):
                    raise BrowserError("INVALID_ARGUMENT", f"fields.{name}.all은 true/false입니다.")
                if "regex" in spec and (not isinstance(spec["regex"], str) or len(spec["regex"]) > 500):
                    raise BrowserError("INVALID_ARGUMENT", f"fields.{name}.regex는 500자 이하 문자열입니다.")
        if options.get("match", "contains") not in MATCH_MODES:
            raise BrowserError("INVALID_ARGUMENT", "match는 contains/exact/regex/prefix입니다.")
        offset, limit = _validate_pagination(options, 50, 1000)
        dedupe = options.get("dedupe", False)
        if dedupe not in (True, False) and not (isinstance(dedupe, list) and all(isinstance(k, str) for k in dedupe)):
            raise BrowserError("INVALID_ARGUMENT", "dedupe는 true 또는 필드 이름 배열입니다.")
        params = {"mode": "records", "item": item, "fields": fields, "text": options.get("text"),
                  "match": options.get("match", "contains"), "includeHidden": bool(options.get("include_hidden", False)),
                  "offset": offset, "limit": limit, "dedupe": dedupe, "maxText": options.get("max_text", 500),
                  **self._scope_params(tab, options.get("container"))}
        if type(params["maxText"]) is not int or not 1 <= params["maxText"] <= 20000:
            raise BrowserError("INVALID_ARGUMENT", "max_text는 1~20000 정수입니다.")
        data = await self._run_query(tab, frame, params)
        data = self.translate_ids(tab, frame, data)
        limits = parse_limits(options, self.config.max_bytes)
        limited = limit_items(data["records"], limits, offset=offset)
        selected = limited["items"]
        result = {"records": selected, "total": data["total"], "matched": data["matched"],
                  "duplicates_removed": data["duplicates_removed"], "detection": data["detection"], "offset": offset,
                  "next_offset": limited["next_offset"] if limited["truncated"] else data["next_offset"],
                  "omitted": max(0, data["matched"] - offset - len(selected)), "frame": params["prefix"],
                  "missing_fields": sum(1 for r in selected if r.get("missing"))}
        result["budget"] = budget_report(result, limits, len(selected), limited["truncated"], limited["truncated_by"],
                                         limited["next_offset"], limited["oversized"])
        return result

    async def page_state(self, tab_id: str | None = None, **options) -> dict:
        """정렬 조건, 활성 필터, 페이지 번호/전체 페이지, 페이지당 수, 전체 결과 수, 토글을 구조화해 반환한다."""
        tab = self.get_tab(tab_id)
        frame = tab.page.main_frame
        compare_with = options.get("compare")
        if compare_with is not None and not isinstance(compare_with, dict):
            raise BrowserError("INVALID_ARGUMENT", "compare는 이전 page.state 응답의 fingerprint 객체입니다.")
        try:
            data = await asyncio.wait_for(frame.evaluate(PAGESTATE, {"prefix": self.frame_prefix(tab, frame)}),
                                          self.config.timeout_ms / 1000)
        except asyncio.TimeoutError as error:
            raise BrowserError("PAGE_UNRESPONSIVE", "페이지 상태 감지가 제한 시간을 넘겼습니다.") from error
        data = self.translate_ids(tab, frame, data)
        sort = data.get("sort") or {}
        fingerprint = {"sort": sort.get("value"), "filters": sorted(f["label"] for f in data.get("filters", [])),
                       "per_page": (data.get("per_page") or {}).get("value"), "query": (data.get("query") or {}).get("value")}
        data["fingerprint"] = fingerprint
        if compare_with is not None:
            differences = {key: {"before": compare_with.get(key), "after": value}
                           for key, value in fingerprint.items() if compare_with.get(key) != value}
            data["preserved"] = not differences
            data["differences"] = differences
        pagination = data.get("pagination") or {}
        data["checks"] = {
            "sort_detected": sort.get("value") is not None,
            "pagination_detected": pagination.get("current_page") is not None and pagination.get("source") is not None,
            "total_results_detected": data.get("total_results") is not None,
            "has_next": pagination.get("has_next"),
        }
        return data
