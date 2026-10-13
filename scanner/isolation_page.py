"""격리환경 `CollectResult` → AI `IsolatedPage` 변환 (이슈 #60).

격리환경(BE 수집)과 AI(페이지 의미 분석)의 경계를 이 함수 하나로
고정한다 — `scanner/fetch.py`·`collect_response.py`는 사실만 모으고,
여기서 AI 계약(`IsolatedPage`)으로 바꾼다. `backend` → `ai` 한 방향
의존(CLAUDE.md 절대 원칙 4)을 지키는 유일한 지점이다: `ai`는 이 모듈을
모르고, 이 모듈만 `ai.types`를 가져온다.

이슈 #60 결정 사항(GitHub #60 코멘트 2절):

| 상황 | page | failure |
| --- | --- | --- |
| Shadow 꺼짐, 수집기 자체를 안 돌림 | `None` | `None` (→ env.answer: not_run) |
| 수집 자체가 실패(`IsolationClientError`) 또는 HTML 없음 | `None` | `COLLECTION_FAILED` |
| HTML 있음 + 실패 사유 있음(`html_truncated`, `browser_*` 등) | 있음 | `PARTIAL_CONTENT` |
| HTML 있음 + 실패 사유 없음 | 있음 | `None` |

"Shadow 꺼짐"/"수집 자체 실패" 두 경우는 이 함수가 아니라 호출부
(`main.py`)가 구분한다 — 이 함수는 **이미 받은 `CollectResult`를 가지고
있는 경우**만 다룬다 (수집을 시도조차 안 한 상태는 `CollectResult` 자체가
없으니 여기 들어오지 않는다).
"""

from datetime import datetime, timezone

from ai.types import FailureCode, IsolatedPage

from scanner.models import CollectResult

# 이슈 #60 결정: brand/category는 수집기가 모르므로 None으로 표현해야
# 한다. 하지만 지금 ai.types.IsolatedPage.brand/category는 아직 `str`
# 필수 필드다(`str | None = None`으로 바꾸는 건 이슈 #60의 AI 담당
# 체크리스트 항목이고, 이 글을 쓰는 시점엔 아직 안 바뀌었다 — 직접
# IsolatedPage(info=...)만 넘겨 ValidationError 로 확인함).
# 그래서 당장은 "unknown"을 쓴다 — Brand("unknown")/Topic("unknown")이
# 둘 다 유효한 UNKNOWN 멤버라 예외 없이 넘어가고,
# ai/src/ai/pipeline/results.py의 build_environment_part()가 실패 사유
# 텍스트에 "unknown, unknown"을 덧붙이는 것 말고는 분석 자체를 막지
# 않는다(이슈 코멘트에서 실제로 확인됨). AI가 필드를 선택형으로 바꾸면
# 이 상수를 None으로 바꾸기만 하면 된다.
_UNKNOWN_IDENTITY = "unknown"


def to_isolated_page(result: CollectResult) -> tuple[IsolatedPage | None, FailureCode | None]:
    """수집 결과를 AI 입력(`IsolatedPage`)으로 바꾼다.

    브라우저(3단계) 결과가 있고 HTML을 담고 있으면 그쪽을 우선한다 — JS
    실행 후 DOM이 2단계 원시 응답보다 실제 화면에 가깝다. HTML이 전혀
    없으면 수집 자체가 실패한 것으로 보고 `None, COLLECTION_FAILED`를
    돌려준다. HTML이 있으면 `failures`가 남아 있어도(`html_truncated`,
    브라우저 쪽 실패 등) `page`는 그대로 넘기고 `PARTIAL_CONTENT`로만
    표시한다 — 부분 수집이어도 실제 찾은 내용은 버리지 않는다.
    """

    browser = result.browser
    html = browser.html if browser and browser.html else result.html
    failures = set(result.failures) | set(browser.failures if browser else ())

    if not html:
        return None, FailureCode.COLLECTION_FAILED

    page = IsolatedPage(
        info=html,
        brand=_UNKNOWN_IDENTITY,
        category=_UNKNOWN_IDENTITY,
        collected_at=datetime.now(timezone.utc),
    )
    return page, (FailureCode.PARTIAL_CONTENT if failures else None)
