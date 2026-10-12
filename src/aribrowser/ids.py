"""Agent가 사용하는 짧은 요소 ID와 디버깅용 긴 내부 ID 사이의 변환을 담당한다.

v0.0.1의 요소 ID는 `t1-f1-72cfea1179b0-<문서 UUID>-e2470`처럼 길어서 Snapshot·Find·
Action에서 반복 참조할 때 토큰을 많이 소비했다. v0.1.0부터 Agent에게는 `t1e12`처럼
탭 접두사와 순번만 있는 짧은 ID를 제공하고, 긴 내부 ID는 `detail`의 `internal_id`
필드로만 노출한다.

- 짧은 ID는 탭마다 독립된 순번을 쓰므로 탭 사이에서 충돌하지 않는다.
- 같은 탭 안의 여러 프레임(iframe)은 순번을 공유하므로 프레임 사이에서도 충돌하지 않는다.
- 같은 내부 ID는 세션 동안 항상 같은 짧은 ID로 변환되므로 재관측 후에도 참조가 유지된다.
- 이전 버전 형식의 긴 ID도 `resolve`가 받아들여 기존 호출과의 호환성을 유지한다.
"""

import re

# 긴 내부 ID 형식: `{탭}-f{프레임 순번}-{프레임 해시}-{문서 UUID}-e{노드 순번}`
INTERNAL_ID = re.compile(r"^(t\d+)-f\d+-[0-9a-f]+-(.+)-e\d+$")
# 짧은 ID 형식: `{탭}e{순번}`
SHORT_ID = re.compile(r"^(t\d+)e\d+$")


class IdRegistry:
    """탭 하나의 짧은 ID ↔ 내부 ID 양방향 매핑과 문서 단위 정리를 담당한다."""

    def __init__(self, tab_id: str):
        """tab_id는 짧은 ID의 접두사가 되며 순번은 1부터 증가한다."""
        self.tab_id = tab_id
        self.sequence = 0
        self.short_by_internal: dict[str, str] = {}
        self.internal_by_short: dict[str, str] = {}
        # 내부 ID가 속한 문서 UUID. 문서가 교체되면 해당 매핑을 정리하기 위해 기억한다.
        self.document_by_short: dict[str, str] = {}

    def short(self, internal_id: str) -> str:
        """내부 ID에 대응하는 짧은 ID를 반환하며 처음 보는 ID에는 새 순번을 배정한다."""
        found = self.short_by_internal.get(internal_id)
        if found:
            return found
        self.sequence += 1
        short = f"{self.tab_id}e{self.sequence}"
        self.short_by_internal[internal_id] = short
        self.internal_by_short[short] = internal_id
        match = INTERNAL_ID.match(internal_id)
        self.document_by_short[short] = match.group(2) if match else ""
        return short

    def resolve(self, element_id) -> str | None:
        """짧은 ID 또는 이전 형식의 긴 ID를 내부 ID로 해석한다. 모르는 ID는 None이다."""
        if not isinstance(element_id, str):
            return None
        if element_id in self.internal_by_short:
            return self.internal_by_short[element_id]
        if element_id in self.short_by_internal:
            return element_id
        return None

    def is_known_format(self, element_id) -> bool:
        """이 탭의 ID 형식인지 판단해 STALE_ELEMENT와 ELEMENT_NOT_FOUND를 구분한다."""
        if not isinstance(element_id, str):
            return False
        return element_id.startswith(self.tab_id + "e") or element_id.startswith(self.tab_id + "-")

    def prune(self, live_documents: set[str]) -> int:
        """현재 프레임에 존재하지 않는 문서의 매핑을 제거하고 제거한 개수를 반환한다.

        탐색으로 문서가 바뀌면 이전 문서의 노드는 다시 나타날 수 없으므로 매핑을
        계속 보관할 이유가 없다. 같은 문서 안에서 잠시 제거된 노드는 다시 연결될 수
        있으므로 문서 단위로만 정리한다.
        """
        stale = [short for short, document in self.document_by_short.items() if document not in live_documents]
        for short in stale:
            internal = self.internal_by_short.pop(short)
            self.short_by_internal.pop(internal, None)
            self.document_by_short.pop(short, None)
        return len(stale)
