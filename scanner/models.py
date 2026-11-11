"""자체 URL Collector 가 돌려주는 자료 모양.

"URL 방문·수집" 사실만 보관한다. 위험도나 판정은 여기서 만들지 않는다.
"""

from dataclasses import dataclass, field

# 격리 서버와 메인 서버가 함께 지키는 상한. 메인 서버는 격리 서버를 믿지 않고 다시 자른다.
MAX_URL_CHARS = 2048
MAX_TITLE_CHARS = 300
MAX_NAVIGATIONS = 10
MAX_NAVIGATION_REQUESTS = 20
MAX_DOWNLOADS = 5
BROWSER_TRIGGERS = frozenset({"always", "status_403_404", "script_redirect", "script_only_page"})


@dataclass(frozen=True)
class BrowserResult:
    """3단계 브라우저 렌더링 결과. 격리 서버에서만 만든다.

    `CollectResult.browser`가 None이면 브라우저 단계를 돌리지 않은 것이고,
    값이 있는데 `failures`가 비어 있지 않으면 돌렸지만 끝까지 보지 못한 것이다.
    빈 `downloads`·0인 `blocked_requests`는 "본 범위에서 없었다"는 뜻일 뿐 안전 판정이 아니다.
    """

    trigger: str  # 브라우저를 돌린 이유: script_only_page / script_redirect / status_403_404 / always
    final_url: str | None
    navigation_chain: tuple[str, ...]  # 메인 프레임이 이동한 주소 (JS 리다이렉트 포함)
    html: str  # JS 실행 후 DOM. 256 KiB 상한
    title: str | None
    downloads: tuple[str, ...]  # 브라우저가 시작하려던 다운로드 URL. 파일은 받지 않는다
    blocked_requests: int  # 허용 목록 밖 요청·WebSocket·팝업을 막은 횟수
    elapsed_ms: int
    failures: tuple[str, ...] = field(default_factory=tuple)
    # 메인 프레임의 이동 요청 전부. 문서가 확정되지 않은 HTTP 리다이렉트 경유까지 담는다.
    navigation_requests: tuple[str, ...] = ()


@dataclass(frozen=True)
class CollectResult:
    input_url: str
    final_url: str | None
    redirect_chain: tuple[str, ...]
    status_code: int | None
    content_type: str | None
    html: str
    title: str | None
    elapsed_ms: int
    failures: tuple[str, ...] = field(default_factory=tuple)
    # 격리 서버의 3단계 결과. None 이면 돌리지 않음(메인 서버 로컬 수집기는 항상 None).
    browser: BrowserResult | None = None

    @classmethod
    def failed(cls, input_url: str, failure: str) -> "CollectResult":
        """아무것도 모으지 못한 결과. 이유는 `failures` 한 칸에 담는다."""
        return cls(input_url, None, (), None, None, "", None, 0, (failure,))


