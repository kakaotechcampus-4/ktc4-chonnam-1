"""읽기(LLM)가 실패했을 때 어휘표로 같은 모양의 ClaimReading 을 만든다 (v3 §5.6).

어휘표로는 바꿔 말한 요구를 다 잡을 수 없다. 그래서 이 결과도 verify 의
V-1~V-7 을 그대로 거친다. 인용은 원문 문장을 그대로 쓴다(V-1을 통과하려면 필요).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar

from ai.case.forms import asserts_acquaintance, asserts_institution, is_recipient_side, is_request
from ai.case.types import (
    BlockerClaim,
    ClaimReading,
    ClaimReadingResult,
    DemandClaim,
    DemandKind,
    IdentityClaim,
    IdentityKind,
    ReadingSource,
)
from ai.types import FailureCode

T = TypeVar("T")


# 순위가 앞선 것이 이긴다. 상품권을 "보내"라고 해도 money_transfer 가 아니라 gift_card 다.
DEMAND_LEXICON: tuple[tuple[DemandKind, re.Pattern[str]], ...] = (
    (DemandKind.GIFT_CARD, re.compile(r"상품권|기프트|핀번호|문상")),
    (DemandKind.AUTH_CODE, re.compile(r"인증\s*번호|OTP|보안\s*코드", re.I)),
    (DemandKind.REMOTE_CONTROL, re.compile(r"팀뷰어|애니데스크|원격")),
    (DemandKind.APP_INSTALL, re.compile(r"설치|다운|앱|어플|apk", re.I)),
    (
        DemandKind.PERSONAL_INFO,
        re.compile(r"(?:주민|계좌|카드|비밀)\s*번호|비번|CVC|신분증|생년월일|이름", re.I),
    ),
    (DemandKind.CASH_WITHDRAWAL, re.compile(r"현금|인출")),
    (DemandKind.MONEY_TRANSFER, re.compile(r"보내|송금|이체|입금|옮기|옮겨|넣어")),
    (
        DemandKind.CALL_NUMBER,
        re.compile(
            r"(?:전화|연락|문의|확인).*\d{2,4}-?\d{3,4}-?\d{4}"
            r"|\d{4}-?\d{4}.*(?:전화|연락|문의)"
        ),
    ),
    (
        DemandKind.MOVE_MESSENGER,
        re.compile(r"이\s*번호로\s*(?:문자|연락)|친구\s*추가|텔레그램"),
    ),
)

# V-5 와 같은 목록. 은행·카드사·통신사·택배사는 일부러 뺐다.
INSTITUTION_PATTERN = re.compile(
    r"검찰|지검|검사(?:님|입니다|실)|수사관|경찰|금융감독원|금감원|금융위원회|법원"
)

# 호칭 뒤에 한글이 더 붙으면 다른 낱말이다(이모티콘, 엄마손파이, 어머니회). 조사·서술어는 허용한다.
_KIN_TAIL = (
    r"(?:께서|이랑|하고|한테|에게|이에요|입니다|인데|이야|예요|이다|은|는|이|가|을|를|의|도|만|랑|와|과|야|아|인|다)?"
    r"(?![가-힣])"
)
ACQUAINTANCE_PATTERN = re.compile(
    r"(?:엄마|아빠|어머니|아버지|아들|딸(?!기)|언니|오빠|누나|동생|이모|삼촌|고모|친구(?!\s*추천))" + _KIN_TAIL
    + r"|형(?:님|아)?(?=[\s,!.]|$)"
)


def identity_kinds(text: str) -> set[IdentityKind]:
    """글에 나온 신원 어휘 종류. 읽기 폴백과 같은 어휘표라 운영·평가가 함께 쓴다."""
    kinds: set[IdentityKind] = set()
    if INSTITUTION_PATTERN.search(text):
        kinds.add(IdentityKind.INSTITUTION)
    if ACQUAINTANCE_PATTERN.search(text):
        kinds.add(IdentityKind.ACQUAINTANCE)
    return kinds


BLOCKER_PATTERN = re.compile(r"고장|액정|통화\s*(?:가|는)?\s*안|전화\s*(?:가|는)?\s*안|못\s*받")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")


def _sentences(text: str) -> list[str]:
    # 끝의 마침표·느낌표는 뗀다. 물음표는 V-3 이 구분하는 정보라 남긴다.
    parts = (p.strip().rstrip(".!。").strip() for p in _SENTENCE_SPLIT.split(text))
    return [p for p in parts if p]


def classify_demand(sentence: str) -> DemandKind | None:
    for kind, pattern in DEMAND_LEXICON:
        if pattern.search(sentence):
            return kind
    return None


def demand_kinds(text: str) -> set[DemandKind]:
    """글의 문장마다 classify_demand 로 고른 요구 종류. 사례 첨부 관문이 수법 단계 신호로 쓴다."""
    return {kind for s in _sentences(text) if (kind := classify_demand(s)) is not None}


def read_with_lexicon(
    source: ReadingSource, failure: FailureCode | None = None
) -> ClaimReadingResult:
    # 화자 구분이 없어 전체를 읽는다(ADR 0007). 받는 사람 쪽 말은 verify 의 V-2 도 다시 거른다
    identity: list[IdentityClaim] = []
    demands: list[DemandClaim] = []
    blockers: list[BlockerClaim] = []
    for seg in source.segments:
        for sentence in _sentences(seg.text):
            if is_recipient_side(sentence):
                # 받는 사람 말로 보이는 문장은 상한(3·6개)을 차지하지 않게 미리 뺀다
                continue
            if (m := INSTITUTION_PATTERN.search(sentence)):
                identity.append(
                    IdentityClaim(kind=IdentityKind.INSTITUTION, name=m.group(0), quote=sentence)
                )
            elif (m := ACQUAINTANCE_PATTERN.search(sentence)):
                identity.append(
                    IdentityClaim(kind=IdentityKind.ACQUAINTANCE, name=m.group(0), quote=sentence)
                )
            if (kind := classify_demand(sentence)) is not None:
                demands.append(DemandClaim(kind=kind, quote=sentence))
            if BLOCKER_PATTERN.search(sentence):
                blockers.append(BlockerClaim(quote=sentence))

    reading = ClaimReading(
        # 칸마다 개수 상한이 있다(ClaimReading). 대본 전체를 읽어 넘치기 쉬우므로
        # 자기소개·부탁 꼴을 먼저 고르고, 고른 것은 원문 순서로 둔다
        identity=_pick(identity, 3, _strong_identity),
        demands=_pick(demands, 6, lambda d: is_request(d.quote)),
        blockers=blockers[:3],
    )
    return ClaimReadingResult(status="fallback", reading=reading, failure=failure)


def _strong_identity(claim: IdentityClaim) -> bool:
    if claim.kind == IdentityKind.INSTITUTION:
        return asserts_institution(claim.quote, INSTITUTION_PATTERN)
    return asserts_acquaintance(claim.quote)


def _pick(items: list[T], n: int, strong: Callable[[T], bool]) -> list[T]:
    ranked = sorted(range(len(items)), key=lambda i: (not strong(items[i]), i))
    return [items[i] for i in sorted(ranked[:n])]
