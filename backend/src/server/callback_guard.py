"""카카오 callbackUrl 검사 (docs/isolation-security.md §5.5 "콜백 주소 검사").

`/kakao/skill` 은 누구나 호출할 수 있고, 요청 본문의 `callbackUrl` 로 결과를 POST 한다.
검사가 없으면 `http://169.254.169.254/...`, `http://localhost:...` 같은 내부 주소로
우리 서버가 요청을 보내게 만들 수 있다 (멘토 리뷰).

허용 조건: https, 포트 443(생략 포함), 사용자 정보(`user@`) 없음, 호스트가 허용 목록에
있음. 허용 목록은 `KAKAO_CALLBACK_HOSTS`(쉼표 구분)로 정한다. 비어 있으면
`kakao.com` 과 그 하위 도메인만 허용한다. 카카오 문서에 콜백 호스트가 적혀 있지
않으므로, 운영 로그의 `[CALLBACK HOST]` 로 실제 호스트를 확인해 정확한 이름으로 좁힌다.
IP 주소 표기는 허용 목록의 이름과 일치하지 않으므로 따로 처리하지 않아도 거부된다.
"""

import os
from urllib.parse import urlsplit

DEFAULT_CALLBACK_SUFFIXES = ("kakao.com",)
MAX_CALLBACK_URL_LENGTH = 2048


def _allowed_hosts() -> tuple[str, ...]:
    raw = os.getenv("KAKAO_CALLBACK_HOSTS", "")
    hosts = tuple(h.strip().lower().rstrip(".") for h in raw.split(",") if h.strip())
    return hosts or DEFAULT_CALLBACK_SUFFIXES


def callback_host(url: object) -> str | None:
    """로그용 호스트. 경로에는 1회용 콜백 토큰이 있어 남기지 않는다."""
    if not isinstance(url, str):
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def is_allowed_callback_url(url: object) -> bool:
    if not isinstance(url, str) or not url or len(url) > MAX_CALLBACK_URL_LENGTH:
        return False
    if "\\" in url or any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc or port not in (None, 443):
        return False
    host = (parts.hostname or "").rstrip(".")
    if not host:
        return False
    configured = os.getenv("KAKAO_CALLBACK_HOSTS", "").strip()
    if configured:
        # 운영에서 확인한 이름으로 좁힌 뒤에는 정확히 일치할 때만 허용한다.
        return host in _allowed_hosts()
    return any(host == suffix or host.endswith("." + suffix) for suffix in DEFAULT_CALLBACK_SUFFIXES)
