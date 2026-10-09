"""격리환경 `/collect` API 응답(JSON)을 `CollectResult`로 바꾼다.

`scanner/isolation_client.py`(HTTP 요청·인증, 윤여경님 담당)가 응답 본문을
`json.loads()`로 파싱한 뒤 이 모듈에 넘기는 흐름을 전제로 한다. 여기서는
그 결과(`dict`)를 검증해서 `CollectResult`로 바꾸는 것만 한다 — 실제 HTTP
요청·인증은 이 모듈의 책임이 아니다.

`scanner/fetch.py`와 같은 원칙: **예외를 올리지 않는다.** 격리환경이
잘못된 JSON을 보냈거나, 필드가 비어 있거나, 타입이 다르거나, HTML이
약속한 크기(128 KiB)를 넘겨도 `failures`에 사실만 남기고 안전한
`CollectResult`를 돌려준다. Shadow Mode 비교용 호출 하나가 이상해졌다고
전체 분석이 죽으면 안 된다.
"""

from scanner.fetch import HTML_LIMIT_BYTES
from scanner.models import CollectResult

# failures 에 이 모듈이 추가로 쓰는 값: invalid_response(응답 자체가
# dict 가 아니거나 핵심 필드를 신뢰할 수 없음), html_truncated(이미
# scanner/fetch.py 에서도 쓰는 값 — 격리환경이 128 KiB 약속을 안 지켰을
# 때 여기서도 한 번 더 자른다).


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _as_int(value: object) -> int | None:
    # bool은 int의 서브클래스라 isinstance(True, int)가 True다 — 제외한다.
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _truncate_html(html: str, limit: int) -> tuple[str, bool]:
    encoded = html.encode("utf-8")
    if len(encoded) <= limit:
        return html, False
    return encoded[:limit].decode("utf-8", errors="replace"), True


def parse_collect_response(data: dict) -> CollectResult:
    """`/collect` 응답 본문을 `CollectResult`로 바꾼다.

    `data`가 dict가 아니면(예: 호출부가 JSON 디코딩에 실패해 `None`을
    넘긴 경우) 빈 값으로 채운 `CollectResult`를 `failures=("invalid_response",)`
    와 함께 돌려준다. `input_url`이 비어 있거나 문자열이 아니어도 같은
    취급이다 — 이 값이 없으면 어떤 요청에 대한 응답인지조차 알 수 없다.

    그 밖의 필드는 타입이 다르거나 없으면 개별적으로 안전한 기본값
    (`None`, 빈 튜플, 빈 문자열)으로 채우고 전체를 실패로 보지 않는다.
    """

    if not isinstance(data, dict):
        return CollectResult(
            input_url="",
            final_url=None,
            redirect_chain=(),
            status_code=None,
            content_type=None,
            html="",
            title=None,
            elapsed_ms=0,
            failures=("invalid_response",),
        )

    failures = list(_as_str_tuple(data.get("failures")))

    input_url = _as_str(data.get("input_url")) or ""
    if not input_url and "invalid_response" not in failures:
        failures.append("invalid_response")

    html = _as_str(data.get("html")) or ""
    html, truncated = _truncate_html(html, HTML_LIMIT_BYTES)
    if truncated and "html_truncated" not in failures:
        failures.append("html_truncated")

    return CollectResult(
        input_url=input_url,
        final_url=_as_str(data.get("final_url")),
        redirect_chain=_as_str_tuple(data.get("redirect_chain")),
        status_code=_as_int(data.get("status_code")),
        content_type=_as_str(data.get("content_type")),
        html=html,
        title=_as_str(data.get("title")),
        elapsed_ms=_as_int(data.get("elapsed_ms")) or 0,
        failures=tuple(failures),
    )
