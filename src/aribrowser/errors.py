"""엔진 예외를 Agent가 재시도 여부를 판단할 수 있는 구조화된 오류로 바꾼다."""

from typing import Any


class BrowserError(Exception):
    """code는 안정적인 기계용 분류이며 details는 문제를 재현할 최소 문맥이다."""

    def __init__(self, code: str, message: str, **details: Any):
        """외부에 반환할 오류 코드, 설명, 추가 정보를 함께 보관한다."""
        super().__init__(message)
        self.code = code
        self.details = details

    def as_dict(self) -> dict:
        """JSON 프로토콜에서 직렬화 가능한 오류 봉투를 생성한다."""
        return {"code": self.code, "message": str(self), "details": self.details}
