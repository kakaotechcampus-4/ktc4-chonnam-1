"""난독화된 문자 본문을 검색용으로 정규화합니다.

검색 전용입니다. LLM에는 원문을 그대로 넘깁니다. 정규화된 텍스트를 주면
LLM이 내는 evidence도 정규화 문자열이 되어 원문 대조가 깨집니다.
"""

import html
import re

_PREFIX_RE = re.compile(r"\[(?:web발신|국외발신)\]", re.IGNORECASE)
_KEEP_RE = re.compile(r"[^0-9a-z가-힣]+")


def normalize(text: str) -> str:
    """구분자·엔티티·발신 접두어를 제거하고 소문자 한글/영숫자만 남깁니다."""
    s = html.unescape(text)
    # 이중 이스케이프 잔재: "Camp;J" → "CJ"
    s = s.replace("amp;", "")
    # 표준 엔티티가 아니라 unescape가 처리하지 못합니다.
    # 먼저 지우지 않으면 keep 필터에서 "lg"가 남습니다.
    s = s.replace("&l;", "").replace("&g;", "")
    s = _PREFIX_RE.sub(" ", s)
    return _KEEP_RE.sub("", s.lower())
