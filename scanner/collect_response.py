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
from scanner.models import BrowserResult, CollectResult

MAX_NAVIGATIONS = 10
MAX_DOWNLOADS = 5

# failures 에 이 모듈이 추가로 쓰는 값:
# - invalid_response: data가 dict가 아니거나, 응답에 input_url이 없음
#   (약속한 계약에 없는 응답이라 핵심 필드조차 못 믿는다)
# - url_mismatch: 응답의 input_url이 우리가 요청한 URL과 다름 (요청·응답이
#   서로 안 맞을 수 있다는 신호라 html 등 나머지 내용도 안 믿는다)
# - html_truncated: scanner/fetch.py 에서도 쓰는 값 — 격리환경이 128 KiB
#   약속을 안 지켰을 때 여기서도 한 번 더 자른다.


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


def _as_str_tuple_capped(value: object, cap: int) -> tuple[str, ...]:
    return _as_str_tuple(value)[:cap]


def _parse_browser(value: object) -> BrowserResult | None:
    """`browser` 필드를 읽는다. 없거나 null 이면 3단계를 돌리지 않은 것(None)이다.

    객체가 아니면 돌렸는지조차 믿을 수 없으므로 `invalid_response`가 붙은 빈 결과로
    남긴다 — None 으로 바꾸면 "실패"가 "미실행"으로 둔갑한다.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        return BrowserResult(
            trigger="", final_url=None, navigation_chain=(), html="", title=None,
            downloads=(), blocked_requests=0, elapsed_ms=0, failures=("invalid_response",),
        )
    failures = list(_as_str_tuple(value.get("failures")))
    html, truncated = _truncate_html(_as_str(value.get("html")) or "", HTML_LIMIT_BYTES)
    if truncated and "html_truncated" not in failures:
        failures.append("html_truncated")
    return BrowserResult(
        trigger=_as_str(value.get("trigger")) or "",
        final_url=_as_str(value.get("final_url")),
        navigation_chain=_as_str_tuple_capped(value.get("navigation_chain"), MAX_NAVIGATIONS),
        html=html,
        title=_as_str(value.get("title")),
        downloads=_as_str_tuple_capped(value.get("downloads"), MAX_DOWNLOADS),
        blocked_requests=_as_int(value.get("blocked_requests")) or 0,
        elapsed_ms=_as_int(value.get("elapsed_ms")) or 0,
        failures=tuple(failures),
    )


def _truncate_html(html: str, limit: int) -> tuple[str, bool]:
    """UTF-8 바이트 기준으로 `limit`을 넘지 않게 자른다.

    `errors="replace"`는 쓰지 않는다 — 멀티바이트 문자(한글 등) 경계에서
    잘리면 대체 문자(U+FFFD, UTF-8로 3바이트)가 끼어들어 최종 바이트 수가
    `limit`을 다시 넘을 수 있다(예: 3바이트 글자의 마지막 1바이트만 잘려도
    대체 문자 자체가 3바이트라 순바이트 증가). `errors="ignore"`는 불완전한
    끝 시퀀스를 그냥 버려서 결과가 `limit`보다 커지는 일이 없다.
    """

    encoded = html.encode("utf-8")
    if len(encoded) <= limit:
        return html, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _invalid(expected_url: str, failure: str) -> CollectResult:
    return CollectResult(
        input_url=expected_url,
        final_url=None,
        redirect_chain=(),
        status_code=None,
        content_type=None,
        html="",
        title=None,
        elapsed_ms=0,
        failures=(failure,),
    )


def parse_collect_response(expected_url: str, data: dict) -> CollectResult:
    """`/collect` 응답 본문을 `CollectResult`로 바꾼다.

    `expected_url`은 우리가 실제로 요청한 URL이다 — 결과의 `input_url`은
    항상 이 값을 쓴다. 응답이 자기 `input_url`로 다른 값을 돌려주면
    요청·응답이 서로 안 맞는다는 뜻이므로(레이스·캐시 버그 등) 신뢰할 수
    없다고 보고 `url_mismatch`로 처리하며, 이때는 html 등 나머지 내용도
    쓰지 않는다 — 어떤 요청에 대한 응답인지가 불확실하면 그 안의 내용도
    믿을 이유가 없다.

    `data`가 dict가 아니거나 `input_url` 필드 자체가 없으면(계약에 없는
    응답) `invalid_response`로 같은 방식으로 처리한다.

    그 밖의 필드는 타입이 다르거나 없으면 개별적으로 안전한 기본값
    (`None`, 빈 튜플, 빈 문자열)으로 채우고 전체를 실패로 보지 않는다.
    """

    if not isinstance(data, dict):
        return _invalid(expected_url, "invalid_response")

    reported_url = _as_str(data.get("input_url"))
    if reported_url is None:
        return _invalid(expected_url, "invalid_response")
    if reported_url != expected_url:
        return _invalid(expected_url, "url_mismatch")

    failures = list(_as_str_tuple(data.get("failures")))

    html = _as_str(data.get("html")) or ""
    html, truncated = _truncate_html(html, HTML_LIMIT_BYTES)
    if truncated and "html_truncated" not in failures:
        failures.append("html_truncated")

    return CollectResult(
        input_url=expected_url,
        final_url=_as_str(data.get("final_url")),
        redirect_chain=_as_str_tuple(data.get("redirect_chain")),
        status_code=_as_int(data.get("status_code")),
        content_type=_as_str(data.get("content_type")),
        html=html,
        title=_as_str(data.get("title")),
        elapsed_ms=_as_int(data.get("elapsed_ms")) or 0,
        failures=tuple(failures),
        browser=_parse_browser(data.get("browser")),
    )
