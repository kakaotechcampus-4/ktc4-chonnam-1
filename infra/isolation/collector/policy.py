"""격리 수집 API의 대상 제한 (docs/isolation-security.md §5.2·§5.3).

카테캠 1단계에서는 팀이 만든 가짜 페이지 호스트만 연다. 최종 방어선은 보안 그룹과
호스트 방화벽이고, 이 검사는 요청을 보내기 전에 같은 목록을 앱에서 한 번 더 확인한다.
"""

import os
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048


def load_allowed_hosts(raw: str | None = None) -> frozenset[str]:
    """`COLLECTOR_ALLOWED_HOSTS`(쉼표 구분)를 소문자·끝 점 제거한 집합으로 읽는다."""
    raw = os.getenv("COLLECTOR_ALLOWED_HOSTS", "") if raw is None else raw
    return frozenset(
        host.strip().lower().rstrip(".") for host in raw.split(",") if host.strip()
    )


def is_allowed_url(url: str, allowed_hosts: frozenset[str]) -> bool:
    """https, 포트 443, 허용 호스트일 때만 True.

    해석이 단계마다 달라질 수 있는 URL(사용자 정보 `user@`, 백슬래시, 공백·제어 문자)은
    호스트가 맞아 보여도 거부한다. 숫자·16진수 IP 표기는 허용 목록의 호스트명과
    일치하지 않으므로 따로 처리하지 않아도 거부된다.
    """
    if not url or len(url) > MAX_URL_LENGTH or "\\" in url:
        return False
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or "@" in parts.netloc:
        return False
    if port not in (None, 443):
        return False
    host = (parts.hostname or "").rstrip(".")
    return host in allowed_hosts
