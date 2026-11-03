"""읽기(LLM·어휘표) 결과 중 실제 대본에 근거가 있는 것만 남긴다 (v3 §5.5 V-1~V-7).

판정은 코드가 한다. 모델이 낸 주장은 인용이 입력에 있고, 받는 사람 쪽 말 꼴이 아니고,
알림·부정문이 아닐 때만 근거가 된다. 버린 것은 사유와 함께 rejected 에 남긴다.

대본에 화자 구분이 없다(ADR 0008). 그래서 V-2 는 화자 대신 문장 꼴(forms)을 본다.
하한에 쓰는 근거(counts_for_floor)는 꼴까지 맞아야 한다: 신원은 자기소개 꼴, 요구는 부탁 꼴.
"""

from __future__ import annotations

import re
from typing import Literal

from ai.case import forms
from ai.case.lexicon import DEMAND_LEXICON, INSTITUTION_PATTERN
from ai.case.types import (
    ClaimReading,
    ClaimView,
    ClaimViewDemand,
    ClaimViewIdentity,
    DemandKind,
    Evidence,
    EvidenceCode,
    IdentityKind,
    Origin,
    ReadingSource,
    RejectedClaim,
)
from ai.links import valid_host
from ai.message_requests import action_is_denied
from ai.types import StrictModel

MIN_QUOTE_CHARS = 4

# v3 §5.2 표
_DEMAND_CODE: dict[DemandKind, EvidenceCode] = {
    DemandKind.MONEY_TRANSFER: EvidenceCode.MONEY_REQUEST,
    DemandKind.CASH_WITHDRAWAL: EvidenceCode.CASH_DELIVERY_REQUEST,
    DemandKind.GIFT_CARD: EvidenceCode.GIFT_CARD_REQUEST,
    DemandKind.AUTH_CODE: EvidenceCode.AUTH_CODE_REQUEST,
    DemandKind.PERSONAL_INFO: EvidenceCode.INFO_REQUEST,
    DemandKind.APP_INSTALL: EvidenceCode.APP_INSTALL_REQUEST,
    DemandKind.REMOTE_CONTROL: EvidenceCode.REMOTE_APP_MENTION,
    DemandKind.CALL_NUMBER: EvidenceCode.CALL_REQUEST,
    DemandKind.MOVE_MESSENGER: EvidenceCode.MESSENGER_MOVE,
}

# v3 §7.1 L-3·L-4 조합에 쓰이는 코드. 나머지는 보조라 하한을 세우지 않는다.
_FLOOR_CODES = frozenset(
    {
        EvidenceCode.INSTITUTION_CLAIM,
        EvidenceCode.ACQUAINTANCE_CLAIM,
        EvidenceCode.MONEY_REQUEST,
        EvidenceCode.APP_INSTALL_REQUEST,
        EvidenceCode.REMOTE_APP_MENTION,
        EvidenceCode.INFO_REQUEST,
        EvidenceCode.AUTH_CODE_REQUEST,
        EvidenceCode.CASH_DELIVERY_REQUEST,
        EvidenceCode.GIFT_CARD_REQUEST,
    }
)

# V-3 알림 문장 끝 모양. 요구가 아니라 이미 일어난 일·상태를 말하는 문장이다.
_NOTICE_ENDINGS = (
    re.compile(r"(?:었|았|였|됐|했|되었)(?:습니다|어요|다)$"),
    re.compile(r"(?:입니다|이에요|예요)$"),
    re.compile(r"(?:되어|돼)\s*있(?:습니다|어요)$"),
)
_NOTICE_DOEMNIDA = re.compile(r"됩니다$")
# "됩니다" 앞이 이 꼴이면 요구다: "입력하시면 됩니다", "송금하셔야 됩니다", "보내셔도 됩니다"
_DOEMNIDA_REQUEST = re.compile(r"(?:면|야|도)$")
# V-4 요구 절의 서술어가 부정·불필요인 꼴. 절 끝에서만 본다.
# "보내지 않으면 정지됩니다" 는 끝 서술어가 부정이 아니라서 걸리지 않는다
_NEGATED_PREDICATE = re.compile(
    r"(?:"
    r"지\s*(?:마(?:세요|십시오|시오|요|라)?|말(?:아|라|고|길)\S*)(?:\s+(?:주|줘)\S*)?"
    r"|지\s*않\S*(?:\s+(?:있|계시|계십|돼|됩|되|괜찮)\S*)?"
    r"|안\s+\S*(?:셔도|해도|아도|어도)\s+(?:돼|됩|되|괜찮)\S*"
    r"|필요\s*(?:가|는)?\s*(?:없|하지\s*않)\S*"
    r"|불필요\S*|금지\S*"
    r")$"
)
_TRAILING_PUNCT = re.compile(r"[.!\s]+$")

_CLAUSE_BREAK = re.compile(r"(?:고(?=\s+(?!있|계시|싶))|며|지만|는데|,)\s+")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")


class VerifyResult(StrictModel):
    accepted: list[Evidence]
    rejected: list[RejectedClaim]
    spoken_hosts: list[str]
    claims: ClaimView


def _norm(text: str) -> str:
    """claims.py 가 모델에게 공백을 접어 보여 주므로 양쪽을 같은 모양으로 맞춘다."""
    return re.sub(r"\s+", " ", text).strip()


def _is_notice_sentence(quote: str) -> bool:
    sentence = _norm(quote)
    if sentence.endswith(("?", "？")):
        return False
    sentence = _TRAILING_PUNCT.sub("", sentence)
    if any(p.search(sentence) for p in _NOTICE_ENDINGS):
        return True
    m = _NOTICE_DOEMNIDA.search(sentence)
    # "발송됩니다" 같은 상태 알림만 거른다. 의무·조건 꼴은 요구라서 통과한다
    return bool(m) and not _DOEMNIDA_REQUEST.search(sentence[: m.start()].rstrip())


def _last_clause_start(quote: str) -> int:
    ends = [m.end() for m in _CLAUSE_BREAK.finditer(quote)]
    return ends[-1] if ends else 0


def _demand_clause(quote: str, sentence: str) -> str:
    """인용의 마지막 절. 서술어가 인용 밖에 있으면("돈을 보내" + "지 마세요") 같은 절 끝까지 잇는다."""
    at = sentence.find(quote)
    rest = sentence[at + len(quote) :] if at >= 0 else ""
    if (m := _CLAUSE_BREAK.search(rest)) is not None:
        rest = rest[: m.start()]
    return _TRAILING_PUNCT.sub("", (quote[_last_clause_start(quote) :] + rest).strip())


def _is_negated(clause: str) -> bool:
    """V-4. 요구 행동 자체가 부정됐는지. 절 끝 서술어나 마지막 행동 낱말 바로 뒤("송금 완료")만 본다.

    절 안의 모든 자리를 보면 "안내에 따라 송금해 주세요" 가 '안내' 때문에, 앞 행동 낱말까지 보면
    "이체 내역 확인 후 다시 보내 주세요" 가 '이체 내역' 때문에 부정으로 잡힌다.
    """
    if _NEGATED_PREDICATE.search(clause):
        return True
    ends = [m.end() for _, pattern in DEMAND_LEXICON for m in pattern.finditer(clause)]
    return bool(ends) and action_is_denied(clause, max(ends))


def _verify_host(raw: str) -> str | None:
    host = _SCHEME.sub("", raw.strip().lower())
    host = re.split(r"[/?#]", host, maxsplit=1)[0]
    # 문자 링크와 같은 호스트 규칙을 쓴다 (ai.links)
    return host if valid_host(host) else None


def _identity_self_side(kind: IdentityKind, sentence: str) -> bool:
    """신원 문장이 받는 사람 쪽 말 꼴인가. 자기소개 꼴이면 물음표가 붙어도 상대 말로 본다."""
    if forms.is_quotation(sentence):
        return True
    if kind == IdentityKind.INSTITUTION and forms.asserts_institution(sentence, INSTITUTION_PATTERN):
        return False
    if kind == IdentityKind.ACQUAINTANCE and forms.asserts_acquaintance(sentence):
        return False
    # "검찰이요?", "경찰이세요?" 처럼 신원을 되묻는 말은 높임이 있어도 받는 사람 말이다
    return forms.is_question(sentence) or forms.is_self_talk(sentence)


def _asserts(kind: IdentityKind, sentence: str) -> bool:
    if kind == IdentityKind.INSTITUTION:
        return forms.asserts_institution(sentence, INSTITUTION_PATTERN)
    if kind == IdentityKind.ACQUAINTANCE:
        return forms.asserts_acquaintance(sentence)
    return False


def verify_call(
    source: ReadingSource,
    reading: ClaimReading,
    *,
    derived_by: Literal["llm", "lexicon"],
) -> VerifyResult:
    texts = [_norm(seg.text) for seg in source.segments]
    accepted: list[Evidence] = []
    rejected: list[RejectedClaim] = []

    def locate(kind: str, quote: str, self_side):
        """V-1·V-2. 통과하면 (인용, 세그먼트 텍스트, 인용 끝 위치, 인용을 품은 문장), 아니면 None.

        같은 인용이 여러 번 나오면(상대가 한 말을 받는 사람이 되풀이) 받는 사람 꼴이 아닌 첫 자리를 쓴다.
        """
        q = _norm(quote)
        if len(q.replace(" ", "")) < MIN_QUOTE_CHARS:
            rejected.append(RejectedClaim(kind=kind, quote=quote, reason="too_short"))
            return None
        found = False
        for text in texts:
            start = text.find(q)
            while start >= 0:
                found = True
                sentence = forms.sentence_at(text, start, start + len(q))
                if not self_side(sentence):
                    return q, text, start + len(q), sentence
                start = text.find(q, start + 1)
        if not found:
            # 원문에 없는 인용은 모델이 만든 글이라 결과에 싣지 않는다
            rejected.append(RejectedClaim(kind=kind, quote="", reason="not_in_source"))
        else:
            rejected.append(RejectedClaim(kind=kind, quote=quote, reason="self_side"))
        return None

    def add(code: EvidenceCode, quote: str, *, shaped: bool = True) -> None:
        accepted.append(
            Evidence(
                code=code,
                origin=Origin.TRANSCRIPT,
                quote=quote,
                derived_by=derived_by,
                counts_for_floor=code in _FLOOR_CODES and shaped,
            )
        )

    # R15 에서 기관명을 찾아볼 문장. 기관을 되묻거나 옮긴 말은 뺀다
    caller_sentences = [
        s
        for text in texts
        for s in forms.sentences(text)
        if not _identity_self_side(IdentityKind.INSTITUTION, s)
    ]

    claims = ClaimView()

    for ident in reading.identity:
        hit = locate(
            "identity", ident.quote, lambda s, k=ident.kind: _identity_self_side(k, s)
        )
        if hit is None:
            continue
        q, sentence = hit[0], hit[3]
        listed = False
        name = ident.name
        if ident.kind == IdentityKind.INSTITUTION:
            # 모델이 적은 name 은 그 기관명이 상대 쪽 말에 실제로 나올 때만 믿는다 (R15).
            # 아니면 인용만으로 판단하고, 이후 단계가 쓸 이름도 인용에서 찾은 낱말로 바꾼다
            m_name = INSTITUTION_PATTERN.search(ident.name)
            named = [s for s in caller_sentences if m_name and m_name.group(0) in s]
            if named:
                listed = True
            elif (m_quote := INSTITUTION_PATTERN.search(q)) is not None:
                listed = True
                name = m_quote.group(0)
            if listed:
                # 하한에는 자기소개 꼴("○○지검 수사관입니다")만 쓴다. 기관을 말하기만 한 문장은 주의로 남는다
                shaped = _asserts(ident.kind, sentence) or any(
                    _asserts(ident.kind, s) for s in named
                )
                add(EvidenceCode.INSTITUTION_CLAIM, q, shaped=shaped)
        elif ident.kind == IdentityKind.ACQUAINTANCE:
            # "엄마 나야", "엄마, 폰 고장 났어" 같은 꼴만 하한에 쓴다. "우리 아들이" 는 남 이야기다
            add(EvidenceCode.ACQUAINTANCE_CLAIM, q, shaped=_asserts(ident.kind, sentence))
        claims.identity.append(
            ClaimViewIdentity(
                kind=ident.kind, name=name, quote=q, listed_institution=listed
            )
        )

    for pretext in reading.pretexts:
        hit = locate("pretext", pretext.quote, forms.is_recipient_side)
        if hit is not None:
            claims.pretexts.append(hit[0])

    for demand in reading.demands:
        hit = locate("demand", demand.quote, forms.is_recipient_side)
        if hit is None:
            continue
        q, _, _, sentence = hit
        if _is_notice_sentence(q):
            rejected.append(
                RejectedClaim(kind="demand", quote=demand.quote, reason="notice_sentence")
            )
            continue
        # 앞 절의 부정("걱정하지 않으셔도 되고")이 뒤 절의 요구를 지우면 안 된다
        if _is_negated(_demand_clause(q, _norm(sentence))):
            rejected.append(
                RejectedClaim(kind="demand", quote=demand.quote, reason="negated")
            )
            continue
        code = _DEMAND_CODE.get(demand.kind)
        if code is not None:
            # 하한에는 부탁 꼴("옮기셔야 합니다", "불러 주세요")만 쓴다. 혼잣말·이야기 속 "보내" 는 주의로 남는다
            add(code, q, shaped=forms.is_request(sentence))
        claims.demands.append(ClaimViewDemand(kind=demand.kind, quote=q))

    for blocker in reading.blockers:
        hit = locate("blocker", blocker.quote, forms.is_recipient_side)
        if hit is not None:
            add(EvidenceCode.CONTACT_BLOCKER, hit[0])
            claims.blockers.append(hit[0])

    hosts: list[str] = []
    for raw in reading.spoken_hosts:
        host = _verify_host(raw)
        if host is None:
            rejected.append(RejectedClaim(kind="spoken_host", quote=raw, reason="not_url"))
        elif host not in hosts:
            hosts.append(host)

    return VerifyResult(
        accepted=accepted,
        rejected=rejected,
        spoken_hosts=hosts,
        claims=claims,
    )
