"""요구 분석 LLM 이 짚은 링크를 검증해 조사에 쓸 정규형으로 바꾼다 (ADR 0007).

링크를 찾는 일은 LLM 이 한다. 문자는 `analyze_message` 의 `urls`(원문에 적힌 그대로의
구간), 통화는 `read_claims` 의 `spoken_hosts`(말로 불러 준 주소를 되살린 호스트)다.
여기서는 찾은 값을 검증하고 모양만 바꾼다. 글자를 고치거나 주소를 새로 만들지 않는다.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from ai.types import LinkCandidate, LinkOrigin

# 필터를 피하려고 바꿔 적은 표기. 사람이 주소창에 옮겨 칠 때 되돌리는 모양만 되살린다
_DEFANG: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"hxxp", re.I), "http"),
    (re.compile(r"[\[({](?:\.|dot)[\])}]", re.I), "."),
    (re.compile(r"\[:\]"), ":"),
    (re.compile(r"[。．｡]"), "."),
    # "cj-deliver . top": 양쪽에 공백을 둔 점만 붙인다. "cj.com. Then" 같은 문장 끝은 그대로 둔다
    (re.compile(r"(?<=[a-z0-9])\s+(?:\.|dot)\s+(?=[a-z0-9])", re.I), "."),
)
# 링크 앞뒤에 붙는 괄호·따옴표·문장 부호와 공백
_EDGE = "\"'()[]{}<>.,!?;:，。！？、 \t\r\n　"
# 앞뒤에 붙어 온 한글은 조사·낱말이다("bit.ly/abc로"). 문자 링크는 한글로 끝나지 않는다
_HANGUL_EDGE = re.compile(r"^[ㄱ-ㅎㅏ-ㅣ가-힣]+|[ㄱ-ㅎㅏ-ㅣ가-힣]+$")
_SCHEME = re.compile(r"([a-z][a-z0-9+.-]*)://", re.I)
_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
# 짚은 구간 바로 앞뒤에 붙어 있으면 주소가 이어진다는 뜻인 글자
_JOINED = frozenset("-_@%~+")
# 이 글자 다음에 영문·숫자가 오면 주소가 이어진다
_SEPARATORS = frozenset("./:?#=&")


def defang(text: str) -> str:
    """무력화 표기(`hxxp`, `[.]`, `(.)`, `[dot]`, `。`, ` . `)를 되살린다."""
    for pattern, replacement in _DEFANG:
        text = pattern.sub(replacement, text)
    return text


def valid_host(host: str) -> bool:
    """소문자 호스트 이름이거나 IPv4 인가. 끝 이름에 영문이 없으면 "3.5" 같은 숫자 표현이다."""
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL.fullmatch(label) for label in labels):
        return False
    if re.search(r"[a-z]", labels[-1]):
        return True
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return True


def _normalize(raw: str) -> tuple[str, str] | None:
    """(url, host). 스킴이 없으면 https, 호스트는 소문자, 경로가 비면 "/" (v3 §4)."""
    candidate = _HANGUL_EDGE.sub("", defang(raw).strip(_EDGE)).strip(_EDGE)
    if not candidate or any(char.isspace() for char in candidate):
        return None
    scheme = _SCHEME.match(candidate)
    if scheme is None:
        if "@" in candidate:
            # 스킴 없는 "아이디@도메인" 은 메일 주소다
            return None
        candidate = f"https://{candidate}"
    elif scheme.group(1).lower() not in ("http", "https"):
        return None
    try:
        parts = urlsplit(candidate)
        host, port = parts.hostname or "", parts.port
    except ValueError:
        return None
    if not valid_host(host):
        return None
    if scheme is None and not re.search(r"[a-z]", host):
        # 스킴 없는 숫자 주소는 버전·금액 같은 숫자 표현과 구별되지 않는다
        return None
    userinfo = parts.netloc.rpartition("@")[0]
    netloc = (f"{userinfo}@" if userinfo else "") + host + (f":{port}" if port is not None else "")
    url = f"{parts.scheme}://{netloc}{parts.path or '/'}"
    if parts.query:
        url += f"?{parts.query}"
    if parts.fragment:
        url += f"#{parts.fragment}"
    return url, host


def _joined(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in _JOINED)


def _continues(ahead: str) -> bool:
    """`ahead` 가 주소의 이어지는 부분으로 시작하는가. 앞쪽은 뒤집어서 넘긴다."""
    if not ahead:
        return False
    if _joined(ahead[0]):
        return True
    return ahead[0] in _SEPARATORS and len(ahead) > 1 and _joined(ahead[1])


def _spots(flat: str, core: str) -> list[tuple[int, int]]:
    spots: list[tuple[int, int]] = []
    start = flat.find(core)
    while start >= 0:
        spots.append((start, start + len(core)))
        start = flat.find(core, start + 1)
    return spots


def message_links(text: str, spans: Iterable[str]) -> list[tuple[str, LinkCandidate]]:
    """문자에서 LLM 이 링크로 짚은 구간 중 원문에 온전한 링크로 있는 것만 원문 순서로 돌려준다.

    구간은 원문에 그대로 있어야 한다. 그래서 글자를 고친 주소(cjlogistlcs → cjlogistics)는
    걸러진다. 더 긴 주소의 일부만 떼어 낸 구간도 버린다. "cjlogistics.com.evil.top" 에서
    앞만 떼면 공식 주소처럼 보이기 때문이다. 다만 같이 짚은 더 긴 링크 안에 든 자리는 잘린
    것으로 치지 않는다. 그러지 않으면 "cjlogistics.com" 옆의 "cjlogistics.co" 를 버리게 된다.
    """
    flat = defang(text)
    candidates: list[tuple[str, str, tuple[str, str]]] = []
    for span in dict.fromkeys(spans):
        normalized = _normalize(span) if span.strip() and span in text else None
        if normalized is not None:
            candidates.append((span, defang(span).strip(_EDGE), normalized))

    covered: list[tuple[int, int]] = []
    found: list[tuple[int, str, LinkCandidate]] = []
    for span, core, (url, host) in sorted(candidates, key=lambda item: len(item[1]), reverse=True):
        spots = _spots(flat, core)
        free = [(start, end) for start, end in spots
            if not any(left <= start and end <= right for left, right in covered)]
        if not free or any(
            _continues(flat[max(0, start - 2):start][::-1]) or _continues(flat[end:end + 2])
            for start, end in free
        ):
            continue
        covered.extend(spots)
        found.append((free[0][0], span, LinkCandidate(url=url, domain=host, origin=LinkOrigin.MESSAGE)))
    return [(span, link) for _, span, link in sorted(found, key=lambda item: item[0])]


# 링크를 뗀 본문에 남으면 링크가 남았다는 표지. 링크를 찾는 데는 쓰지 않는다
_LINK_MARKS = ("://", "www.")
# 구간 바로 앞에 남는 스킴. 무력화 표기(hxxps, [:])까지
_LEFT_SCHEME = r"(?i:h(?:tt|xx)ps?(?::|\[:\])//)?"


def has_link_mark(text: str) -> bool:
    """`://` 나 `www.` 가 남았는가. 무력화 표기(`hxxp`, `[.]`)도 되살려 본다."""
    flat = defang(text).lower()
    return any(mark in flat for mark in _LINK_MARKS)


def remove_spans(text: str, spans: Iterable[str]) -> str:
    """받아들인 링크 구간을 본문에서 뗀다. split_message 처럼 지우고 앞뒤 공백을 다듬는다.

    LLM 이 스킴을 빼고 짚은 구간("https://bit.ly/a" 의 "bit.ly/a")은 바로 앞 스킴도 함께 뗀다.
    """
    for span in sorted(set(spans), key=len, reverse=True):
        text = re.sub(_LEFT_SCHEME + re.escape(span), "", text)
    return text.strip()


def spoken_link(host: str) -> LinkCandidate | None:
    """통화에서 말로 불러 준 호스트를 링크로 만든다. 철자는 확인할 수 없다 (v3 U-3)."""
    normalized = _normalize(host)
    if normalized is None:
        return None
    url, domain = normalized
    return LinkCandidate(url=url, domain=domain, origin=LinkOrigin.SPOKEN)
