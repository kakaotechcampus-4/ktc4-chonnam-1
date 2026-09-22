"""Source-preserving request context shared by message taxonomy and signals."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re

from ai.kb.normalize import normalize


@dataclass(frozen=True)
class SourceChar:
    value: str
    start: int
    end: int


_ENTITY = re.compile(r"&(?:#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]+);")
_KEEP_CHAR = re.compile(r"[0-9a-z가-힣]", re.I)
_QUOTATION = re.compile(r'''‘[^’]*’|“[^”]*”|'[^']*'|"[^"]*"|「[^」]*」|『[^』]*』''')
_MENTION = re.compile(r"^\s*(?:라는|이라는|이라고\s*(?:한|하는))\s*(?:문구|메시지|안내|표현)")
_WARNING = re.compile(r"무시\s*(?:하|해)|(?:따르|응하|설치하|입력하|클릭하|조회하)지\s*(?:말|마|않)")
_CLAUSE_END = re.compile(r"[.!?\n。！？]")
_NON_REQUEST = re.compile(
    r"^(?:을|를|은|는|이|가|도|할|하는|하기)?"
    r"(?:(?:요청|부탁)(?:을|를|은|는|이|가)?)?"
    r"(?:하지(?:말|마|않)|필요(?:가|는)?(?:없|하지않)|불필요|금지|"
    r"완료(?:되었|됐|됨|입니다|되었습니다|됐습니다|$)|성공|종료|상태|여부|내역|방법|안내|notrequired)"
)
_COMPLETION = re.compile(
    r"^(?:(?:처리|작업)(?:[이가을를])?)?"
    r"(?:(?:정상적으로|성공적으로|모두))?(?:[이가을를])?"
    r"완료(?:$|입니다|되었|됐|했|하였|됨|(?:안내|알림)(?:입니다)?)"
)
_REQUEST = re.compile(
    r"^(?:을|를)?(?:하(?:세요|십시오|라)|해(?:주세요|주십시오|주시기바랍니다)|"
    r"주세요|주십시오|야(?:합니다|해요)|해야(?:합니다|해요)|"
    r"완료(?:하(?:세요|십시오)|해(?:주세요|주십시오))|"
    r"바랍니다|(?:이|가)?필요(?:합니다|해요)|"
    r"(?:부탁|요청)(?:드립니다|합니다|하(?:세요|십시오))|받(?:으세요|아주세요)|please\b|now\b)"
)
_COORDINATOR = re.compile(r"^(?:하고|한뒤|후|및)")
# Only an adjacent, named request may share its imperative with an earlier
# action. Do not search forward for an unrelated affirmative action.
_FOLLOWING_ACTION = re.compile(
    r"(?:(?:주소(?:지)?(?:를)?)(?:확인|입력|변경|수정)|"
    r"(?:앱|어플)(?:을)?(?:설치|다운로드)|"
    r"(?:비밀번호|인증번호)(?:를)?(?:입력|제공|전달)|"
    r"설치|다운로드|내려받|입력|전달|제공|제출|보내|연결|접속|install|download|enter|send|connect)"
)


def normalized_with_positions(text: str) -> tuple[str, list[SourceChar]]:
    decoded: list[SourceChar] = []
    index = 0
    while index < len(text):
        if text[index:index + 3].lower() in {"&l;", "&g;"}:
            index += 3
            continue
        entity = _ENTITY.match(text, index)
        if entity is not None:
            value = html.unescape(entity.group())
            if value != entity.group():
                decoded.extend(SourceChar(char, index, entity.end()) for char in value)
                index = entity.end()
                continue
        if text[index:index + 4].lower() == "amp;":
            index += 4
            continue
        decoded.append(SourceChar(text[index], index, index + 1))
        index += 1
    kept = [item for item in decoded if _KEEP_CHAR.fullmatch(item.value)]
    return "".join(item.value.lower() for item in kept), kept


def request_context(text: str) -> str:
    """Mask reported warnings without moving independent source positions."""
    characters = list(text)
    for quote in _QUOTATION.finditer(text):
        end = _CLAUSE_END.search(text, quote.end())
        remainder = text[quote.end():end.start() if end else len(text)]
        mention = _MENTION.match(remainder)
        if mention and _WARNING.search(remainder[mention.end():]):
            characters[quote.start():quote.end()] = " " * (quote.end() - quote.start())
    return "".join(characters)


def _action_tail(text: str, action_end: int) -> str:
    end = _CLAUSE_END.search(text, action_end)
    return normalize(text[action_end:end.start() if end else len(text)])


def action_is_denied(text: str, action_end: int) -> bool:
    tail = _action_tail(text, action_end)
    return bool(_NON_REQUEST.match(tail) or _COMPLETION.match(tail))


def action_requested(text: str, action_end: int) -> bool:
    """Require a local imperative, including an adjacent coordinated request."""
    tail = _action_tail(text, action_end)
    while tail:
        if _NON_REQUEST.match(tail) or _COMPLETION.match(tail):
            return False
        if _REQUEST.match(tail):
            return True
        coordinator = _COORDINATOR.match(tail)
        following = tail[coordinator.end():] if coordinator else tail
        action = _FOLLOWING_ACTION.match(following)
        if action is None:
            return False
        tail = following[action.end():]
    return False
