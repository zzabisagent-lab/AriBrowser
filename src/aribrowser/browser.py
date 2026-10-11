"""DOM 관측, Chromium 접근성 보강, 안정적인 요소 조회와 차이 반환을 구현한다."""

import asyncio
import copy
from pathlib import Path

from playwright.async_api import Error as PlaywrightError

from .engine import Engine, Tab
from .errors import BrowserError
from .snapshot import compare, descendants, select_view, text_screen

COLLECTOR = Path(__file__).with_name("collector.js").read_text(encoding="utf-8")


class Browser(Engine):
    """엔진 상태 위에 Agent가 읽는 의미 화면과 요소 조작용 참조를 제공한다."""

    async def observe(self, tab: Tab) -> dict:
        """각 프레임을 독립적으로 읽어 교차 출처 iframe과 open Shadow DOM을 합친다."""
        elements, frames, warnings = [], [], []
        tab.elements = {}
        current_frames = tab.page.frames
        for removed in set(tab.frames) - set(current_frames):
            del tab.frames[removed]
        for frame in current_frames:
            if frame not in tab.frames:
                tab.frames[frame] = f"f{len(tab.frames) + 1}-{id(frame):x}"
            prefix = f"{tab.id}-{tab.frames[frame]}"
            try:
                data = await asyncio.wait_for(frame.evaluate(COLLECTOR, {"prefix": prefix}),
                                              self.config.timeout_ms / 1000)
                parent_id, offset_x, offset_y = None, 0, 0
                frame_visible = True
                if frame != tab.page.main_frame:
                    host = await frame.frame_element()
                    try:
                        parent_id = await host.evaluate("el => window.__aribrowser_v001__?.nodeIds.get(el) || null")
                        box = await host.bounding_box()
                        frame_visible = box is not None
                        if box:
                            # iframe 내부 원점은 border 안쪽이므로 테두리 폭을 더한다.
                            border = await host.evaluate("el => ({x: el.clientLeft, y: el.clientTop})")
                            offset_x, offset_y = box["x"] + border["x"], box["y"] + border["y"]
                    finally:
                        await host.dispose()
                frames.append({"id": prefix, "url": frame.url, "parent_element": parent_id,
                               "document_id": data["document_id"], "viewport": data["viewport"]})
                if data["truncated"]:
                    warnings.append({"code": "DOM_LIMIT", "frame": prefix, "visited": data["visited"]})
                for item in data["elements"]:
                    if not item["parent"]:
                        item["parent"] = parent_id
                    item["frame"] = prefix
                    item["bounds"]["x"] += offset_x
                    item["bounds"]["y"] += offset_y
                    viewport = tab.page.viewport_size or {"width": self.config.width, "height": self.config.height}
                    box = item["bounds"]
                    item["state"]["visible"] &= frame_visible
                    item["state"]["in_viewport"] &= frame_visible and box["x"] < viewport["width"] and box["y"] < viewport["height"] and box["x"] + box["width"] > 0 and box["y"] + box["height"] > 0
                    tab.elements[item["id"]] = frame
                    elements.append(item)
                await self._accessibility(tab, frame, data["elements"], warnings)
            except (PlaywrightError, asyncio.TimeoutError) as error:
                warnings.append({"code": "FRAME_UNAVAILABLE", "frame": prefix, "message": str(error)})
        if not frames:
            raise BrowserError("PAGE_UNRESPONSIVE", "페이지를 관측하지 못했습니다.", warnings=warnings)
        return {"page": {"tab": tab.id, "profile": tab.profile, "url": tab.page.url,
                         "title": await tab.page.title(), "loading": tab.loading, "http_status": tab.status,
                         "viewport": tab.page.viewport_size,
                         "authentication": "required" if tab.status in {401, 403} else
                         "login_form_detected" if any(e.get("input_type") == "password" for e in elements) else "unknown"},
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

    async def snapshot(self, tab_id: str | None = None, **options) -> dict:
        """전체 상태를 기준선으로 저장하되 응답은 요청한 전체/부분/변경분만 제공한다."""
        tab = self.get_tab(tab_id)
        state = await self.observe(tab)
        diff = compare(tab.previous, state)
        event_data = self.event_view(tab.event_cursor)
        # 옵션 검증 실패가 기존 기준선을 소비하지 않도록 뷰를 먼저 생성한다.
        mode = options.get("mode", "full")
        result = {"changes": diff, **event_data, "page": state["page"], "warnings": state["warnings"]}
        if mode != "changes":
            result = {**select_view(state, options), **event_data}
            result["text"] = text_screen(result)
        tab.previous = copy.deepcopy(state)
        tab.event_cursor = event_data["cursor"]
        return result

    async def element_handle(self, tab: Tab, element_id: str):
        """선택 당시의 실제 DOM 노드만 반환하며 이름이 같은 새 요소로 재탐색하지 않는다."""
        frame = tab.elements.get(element_id)
        if frame is None:
            code = "STALE_ELEMENT" if isinstance(element_id, str) and element_id.startswith(tab.id + "-") else "ELEMENT_NOT_FOUND"
            raise BrowserError(code, "현재 문서에 해당 요소 ID가 없습니다.", element=element_id)
        try:
            handle = await frame.evaluate_handle("id => window.__aribrowser_v001__?.get(id) || null", element_id)
            element = handle.as_element()
            if element is None:
                await handle.dispose()
                raise BrowserError("STALE_ELEMENT", "요소가 제거되었거나 문서가 바뀌었습니다.", element=element_id)
            return element
        except PlaywrightError as error:
            raise BrowserError("STALE_ELEMENT", str(error), element=element_id) from error

    async def find(self, tab_id: str | None = None, **options) -> dict:
        """텍스트/역할/영역/조작 가능 여부로 검색하며 숨겨진 요소도 선택적으로 포함한다."""
        state = await self.observe(self.get_tab(tab_id))
        elements = state["elements"]
        if options.get("region"):
            if not any(e["id"] == options["region"] for e in elements):
                raise BrowserError("ELEMENT_NOT_FOUND", "영역을 찾지 못했습니다.")
            scope = descendants(elements, options["region"])
            elements = [e for e in elements if e["id"] in scope]
        if not options.get("include_hidden", True):
            elements = [e for e in elements if e["state"]["visible"]]
        query = options.get("text", "").casefold()
        roles = options.get("role")
        roles = [roles] if isinstance(roles, str) else roles
        matches = []
        for element in elements:
            if query not in (element["name"] + " " + element["text"] + " " + str(element.get("value", ""))).casefold():
                continue
            if roles and element["role"] not in roles:
                continue
            if options.get("actionable") and not (element["actions"] and element["state"]["actionable"]):
                continue
            matches.append(element)
        offset, limit = options.get("offset", 0), options.get("limit", 100)
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 10000:
            raise BrowserError("INVALID_ARGUMENT", "검색 offset/limit이 범위를 벗어났습니다.")
        return {"elements": matches[offset:offset + limit], "total": len(matches),
                "next_offset": offset + limit if offset + limit < len(matches) else None,
                "omitted": max(0, len(matches) - offset - limit), "warnings": state["warnings"]}

    async def detail(self, element_id: str, tab_id: str | None = None, radius: int = 2) -> dict:
        """요소 상세, 의미상 조상, 앞뒤 문맥, 직계 자식을 함께 반환한다."""
        if type(radius) is not int or not 0 <= radius <= 100:
            raise BrowserError("INVALID_ARGUMENT", "radius는 0~100 정수입니다.")
        tab = self.get_tab(tab_id)
        state = await self.observe(tab)
        by_id = {e["id"]: e for e in state["elements"]}
        if element_id not in by_id:
            raise BrowserError("STALE_ELEMENT", "요소가 현재 문서에 없습니다.", element=element_id)
        element = by_id[element_id]
        ancestors, parent = [], element.get("parent")
        while parent in by_id:
            ancestors.append(by_id[parent])
            parent = by_id[parent].get("parent")
        index = state["elements"].index(element)
        return {"element": element, "ancestors": ancestors,
                "context": state["elements"][max(0, index - radius):index + radius + 1],
                "children": [e for e in state["elements"] if e.get("parent") == element_id]}

    async def table(self, element_id: str, tab_id: str | None = None, rows=None, columns=None) -> dict:
        """표 ID 기준으로 0부터 시작하는 행/열 인덱스만 선택하고 생략 수를 반환한다."""
        state = await self.observe(self.get_tab(tab_id))
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
            result.append({"id": row["id"], "index": index, "cells": selected})
        return {"id": element_id, "rows": result, "total_rows": len(all_rows),
                "omitted_rows": len(all_rows) - len(result), "omitted_cells": skipped_cells}
