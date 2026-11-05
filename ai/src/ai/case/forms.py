"""문장 꼴로 누가 한 말인지 가늠한다 (ADR 0008). 화자 분리를 대신한다.

대본에는 화자 구분이 없다. 받는 사람이 되묻거나 옮긴 말("검찰이라고요?", "보내라고요?")과
자기 일을 말한 문장("제가 보낼게요")은 꼴로 걸러 낸다. 하한(L-3)은 자기소개 꼴의 신원 주장과
부탁 꼴의 요구가 함께 있을 때만 세운다. 받는 사람이 검찰·지인이라고 자기를 소개하거나
상대에게 송금을 부탁하는 일은 드물기 때문이다. 판정은 하지 않는다. 정규식만 쓴다.
"""

from __future__ import annotations

import re

# 문장 끝. 숫자 사이의 점("010.1234")은 끝이 아니다
_SENTENCE_END = re.compile(r"[.?!…](?=\s|$)")
_TRAILING = re.compile(r"[\s.!…~]+$")
_INTERJECTION = r"^(?:(?:아|어|네|예|응)\s+)?"
# ㄹ 받침 글자. "보내래요" 는 옮긴 말이고 "보낼래요", "보내줄래요" 는 묻거나 부탁하는 말이다
_RIEUL = "".join(chr(0xAC00 + i * 28 + 8) for i in range(399))

# 옮긴 말·되묻기. "~라고 합니다" 는 자기소개라 뺀다
_QUOTATION = re.compile(
    r"(?:라|다|냐|자)\s?(?:고|구)(?=요|[?？]|\s*$|\s+(?:했|하셨|하던|하더|하는|그러|그랬|말))"
    r"|(?:라|다)(?:면서|며)(?:요)?(?=[\s?？.!]|$)"
    r"|(?:라|다)는\s*(?:거|건|게|말|얘기|소리)"
    rf"|(?:(?<![{_RIEUL}])|(?<=말))(?:래|대)요(?=[\s?？.!]|$)"
)
# "돈 보낼래요?" 는 상대에게 묻는 말, "돈 보낼래요." 는 자기 뜻을 말하는 문장이다
_L_RAE = re.compile(rf"(?<!말)(?<=[{_RIEUL}])래요?[?？]?$")
# "보낼 수 있어?" 는 상대가 할 수 있는지 묻는 말이다
_CAN_ASK = re.compile(rf"[{_RIEUL}]\s*수\s*있(?:어|나|니|죠|을까|겠)[가-힣]*[?？]?$")
# "돈 보내면 안 될까?", "50만원 입금 가능해요?" 도 그렇다. 받는 사람도 흔히 하는 말이라
# ("내일 입금하면 안 될까요?") 돈을 집어 말할 때만 본다
MONEY_WORD = r"돈|(?:\d|만|천|백)\s*원|계좌"
_MONEY_ASK = re.compile(r"면\s*안\s*될까요?[?？]?$|가능(?:해|하|할|한)[가-힣]*[?？]?$")
_MONEY_WORD = re.compile(MONEY_WORD)
_QUESTION = re.compile(r"(?:[?？]|나요|까요|냐)$")
# 끝 세 어절에 높임·해 달라는 말이 있으면 받는 사람이 자기 일을 되묻는 말이 아니다.
# "주" 는 뒤에 어미가 붙을 때만 본다("주민번호" 는 아니다). "주겠죠?" 는 짐작하는 말이라 뺀다
_LISTENER = re.compile(r"[시세셔실]|주(?=[세시실면라어고])|주겠(?:어|니|나|습)|줘|줄")
# 그중 해 달라는 물음: "불러 주시겠어요?", "어떻게 되세요?". "주시는 건가요?" 는 되묻기라 아니다
_ASK = re.compile(r"시겠|실래|실\s*수|세요|시죠|십니까|줄래|줄\s*수|주면\s*안|줘")
_PROMISE = re.compile(r"(?:게|께)요?$")
# "제가 대신 입금해 줄까요?" 는 받는 사람의 제안이다. "내가 말한 계좌로 입금해 줄래?" 는
# "내가" 가 뒤 말을 꾸미는 부탁이라, 제안으로 묻는 어미 "줄까"·"드릴까" 만 본다
_OFFER_SUBJECT = re.compile(_INTERJECTION + r"(?:제가|내가)(?![가-힣])")
_OFFER_END = re.compile(r"(?:줄까|드릴까)요?[?？]?$")
_FIRST_PERSON = re.compile(
    _INTERJECTION + r"(?:(?:제가|내가|저는|나는|저도|나도)(?![가-힣])"
    r"|(?:제|내)\s*(?:번호|전화번호|핸드폰|휴대폰|연락처))"
)
_REQUEST = re.compile(
    r"(?:세요|셔요|십시오|시오|주세요|줘요|줘|주라|해라|거라|어라|아라)(?=[\s.!?？~]|$)"
    r"|(?:셔야|어야|아야|여야|해야)\s*(?:해|돼|되|합니다|됩니다|하고|할|될)"
    r"|면\s*(?:돼|됩니다|되세요|되요|되고)"
    r"|주시(?:겠|고|면|기|는|길)|주실\s*(?:래|수|건)|줄\s*(?:수|래)"
    # "입금하시면 확인해 드립니다", "입금해 주면" 같은 조건 부탁. "~하시면 안 됩니다" 는 V-4 가 부정으로 거른다
    r"|(?:주|시)면(?![가-힣])"
    r"|부탁\s*(?:드|해|합)|바랍니다"
)

# 기관 낱말 뒤에 조사가 붙으면 남 이야기다("경찰이", "금감원은", "검찰에").
# "~에서" 는 연락했다는 말("금감원에서 연락드렸습니다")일 때만 자기소개로 본다
# "~에 있는 ○○입니다", "~의 ○○입니다" 처럼 소속을 꾸미는 조사는 막지 않는다
_PARTICLE_END = re.compile(r"(?:에게|한테|께서|으로|로|을|를|이|가|은|는|와|과|랑|도|만)$")
_MEMBER_OF = re.compile(r"에\s*(?:있는|계신|근무|소속|재직)")
# 직원이 아니라고 하거나 기관을 이용하는 사람의 말은 자기소개가 아니다. 자기소개가 끝난 뒤의 말은
# 보지 않는다("금융감독원 김대리입니다 고객님 명의로"). 구두점 없는 받아쓰기에서 흔하다
_NOT_STAFF = re.compile(
    r"아니|가입자|납세자|수급자|민원인|피해자|이용자|신고자|신고인|구직자|고객(?!센터|상담|지원)"
)
# "저희 서울중앙지검으로 오셔서" 처럼 기관을 "저희" 로 부르면 그 기관 사람이 하는 말이다
_OURS = re.compile(r"저희\s*(?:\S+\s+)?\S*$")
_WORD_END = r"(?=[\s,.?!…~]|$)"
_SELF_INTRO = re.compile(
    r"(?:입니다|이에요|예요|이예요|인데요?|이고요?|이구요|라고\s*합니다)" + _WORD_END
)
_CONTACT = re.compile(r"(?:연락|전화)\s*(?:드렸|드립|드려)|나왔(?:습니다|어요|는데)")
_CASUAL_END = r"(?:이거든요?|이며|이야|이라서요?)"
_INTRO_END = re.compile(rf"(?:{_SELF_INTRO.pattern})|{_CASUAL_END}{_WORD_END}|{_CONTACT.pattern}")
# "저는 경찰 아들입니다", "저는 경찰서 가는 길입니다" 는 받는 사람 말일 수 있어
# "저는" 으로 시작하면 기관 뒤에 직함이 있거나 기관 낱말로 바로 끝나야 자기소개로 본다
_TITLE = (
    r"(?:직원|담당자?|담당관|수사관|수사대|수사팀|수사과|조사관|검사관|집행관|감독관|주무관|사무관|서기관"
    r"|경위|경감|경사|경장|경정|경무관|총경|순경|형사|검사|판사|공무원|요원|상담사|상담원|사원"
    r"|대리|주임|계장|과장|팀장|반장|실장|차장|부장|국장|소속)"
)
_ENDING = rf"(?:입니다|이에요|예요|이예요|인데요?|이고요?|이구요|라고\s*합니다|{_CASUAL_END})"
_TITLED = re.compile(_TITLE + _ENDING + _WORD_END)
_DIRECT = re.compile(rf"(?:청|서)?(?:{_SELF_INTRO.pattern})")
# 소속을 꾸미는 "~에서" 는 직함이나 이름으로 바로 끝날 때만 본다("국세청에서 나온 직원입니다").
# "경찰청에서 일하는 친구입니다", "국세청에서 나온 서류 때문에 전화한 사람입니다" 는 아니다
_AFFILIATED = re.compile(
    r"에서\s*(?:나온|근무하는|일하는|파견된)\s+(?:"
    rf"(?:\S+\s+)?\S*{_TITLE}{_ENDING}"
    r"|(?!어머니|아버지)[가-힣]{3}(?:입니다|이에요|라고\s*합니다)"
    rf"){_WORD_END}"
)
# "저는 김민수 검사입니다". 이름은 세 글자만 받는다("저는 내일 건강 검사입니다", "코로나 검사")
_PROSECUTOR = re.compile(
    _INTERJECTION + r"(?:저는|제가|나는)\s+(?:(?!코로나)[가-힣]{3}\s+)?검사입니다" + _WORD_END
)
_BREAK = re.compile(r"[,.?!…]")
# 기관 낱말 뒤 이 글자 수 안에서 자기소개가 끝나야 한다("서울중앙지검 금융범죄수사팀 김○○ 수사관입니다")
_MAX_INTRO_TAIL = 24

_SERVICE_TALK = re.compile(r"고객님|성함|명의자|생년월일|상담원")
_KIN = r"(?:엄마|아빠|어머니|아버지|아들|딸(?!기)|언니|오빠|누나|동생|이모|삼촌|고모|친구|형(?:님)?)"
_ACQ_ASSERT = (
    # "엄마 나야", "저예요"
    re.compile(r"(?<![가-힣])(?:나(?:야|예요|에요)|저(?:예요|에요))(?![가-힣])"),
    # "나 아들인데", "딸이야". 앞에 "우리·내·제·저희" 가 붙으면 남 이야기다
    re.compile(
        r"(?<!우리 )(?<!저희 )(?<!내 )(?<!제 )(?<![가-힣])" + _KIN
        + r"(?:이야|야|인데|이에요|예요|입니다|이고)(?![가-힣])"
    ),
    # 문장 첫머리 부름말: "엄마, 폰 고장 났어", "엄마 나 급해"
    re.compile(
        _INTERJECTION + _KIN
        + r"(?:\s*,|\s+(?:나|저|내|제)(?![가-힣])|\s+(?:폰|핸드폰|휴대폰|전화))"
    ),
)


def _bare(sentence: str) -> str:
    return _TRAILING.sub("", sentence.strip())


def sentence_at(text: str, start: int, end: int) -> str:
    """text 에서 [start, end) 를 품은 문장."""
    left = 0
    for m in _SENTENCE_END.finditer(text, 0, start):
        left = m.end()
    m = _SENTENCE_END.search(text, max(end - 1, 0))
    right = m.end() if m else len(text)
    return text[left:right].strip()


def sentences(text: str) -> list[str]:
    out, left = [], 0
    for m in _SENTENCE_END.finditer(text):
        out.append(text[left : m.end()].strip())
        left = m.end()
    out.append(text[left:].strip())
    return [s for s in out if s]


def is_quotation(sentence: str) -> bool:
    return bool(_QUOTATION.search(sentence))


def is_question(sentence: str) -> bool:
    return bool(_QUESTION.search(_bare(sentence)))


def _listener_marked(sentence: str) -> bool:
    tail = " ".join(_bare(sentence).split()[-3:])
    return bool(_LISTENER.search(tail))


def is_request(sentence: str) -> bool:
    """상대에게 해 달라는 꼴. 높임 물음("어떻게 되세요?")도 부탁으로 본다."""
    if is_question(sentence):
        return bool(_ASK.search(" ".join(_bare(sentence).split()[-3:])))
    return bool(_REQUEST.search(sentence))


def is_self_talk(sentence: str) -> bool:
    """받는 사람이 자기 일을 말하거나 되묻는 꼴: "보내야 돼요?", "제가 보낼게요"."""
    s = _bare(sentence)
    if is_question(s):
        if _OFFER_SUBJECT.search(s) and _OFFER_END.search(s):
            return True
        asks = _L_RAE.search(s) or _CAN_ASK.search(s) or (_MONEY_ASK.search(s) and _MONEY_WORD.search(s))
        if asks and not _FIRST_PERSON.search(s):
            return False
        return not _listener_marked(s)
    # "입금해 주면 오늘 갚을게" 처럼 부탁이 함께 있으면 상대 말이다
    return bool(
        _PROMISE.search(s) or _FIRST_PERSON.search(s) or _L_RAE.search(s)
    ) and not _REQUEST.search(s)


def is_recipient_side(sentence: str) -> bool:
    """받는 사람 쪽 말로 보이는 문장. 근거·번호 대조에 쓰지 않는다."""
    return is_quotation(sentence) or is_self_talk(sentence)


def asserts_institution(sentence: str, pattern: re.Pattern[str]) -> bool:
    """기관 낱말 바로 뒤 절이 자기소개로 끝나는가: "서울중앙지검 수사관입니다", "검찰청인데"."""
    first_person = bool(_FIRST_PERSON.search(sentence))
    for m in pattern.finditer(sentence):
        if _OURS.search(sentence[: m.start()]):
            return True
        # 패턴이 어미까지 품은 "검사입니다". "저는" 이 없으면("정기 검사입니다") 직함인지 알 수 없다
        if _SELF_INTRO.search(m.group(0)) and _PROSECUTOR.search(sentence):
            return True
        # "제가 경찰이에요?" 는 되묻는 말이다
        if first_person and is_question(sentence):
            continue
        after = sentence[m.end() :]
        rest = re.match(r"\S*", after).group(0)
        brk = _BREAK.search(after)
        window = after[: brk.start() if brk else len(after)][:_MAX_INTRO_TAIL]
        end = _INTRO_END.search(window)
        head = window[: end.end()] if end else window
        if _NOT_STAFF.search(head):
            continue
        if rest.endswith("에") and not _MEMBER_OF.search(window):
            continue
        if _AFFILIATED.search(window):
            return True
        contact = _CONTACT.search(window)
        if contact and "에서" in window[: contact.start()]:
            return True
        if "에서" in rest or _PARTICLE_END.search(rest):
            continue
        if first_person:
            if _DIRECT.match(after) or _TITLED.search(head):
                return True
        elif _SELF_INTRO.search(window):
            return True
    return False


def asserts_acquaintance(sentence: str) -> bool:
    s = sentence.strip()
    # 상담원이 고객을 "어머니" 라고 부르는 말은 가족이라는 주장이 아니다
    if _SERVICE_TALK.search(s):
        return False
    return any(p.search(s) for p in _ACQ_ASSERT)
