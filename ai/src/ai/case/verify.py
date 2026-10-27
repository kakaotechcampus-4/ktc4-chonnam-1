"""읽기(LLM·어휘표) 결과 중 실제 대본에 근거가 있는 것만 남긴다 (v3 §5.5 V-1~V-8).

판정은 코드가 한다. 모델이 낸 주장은 인용이 입력에 있고, 상대 화자의 말이고,
알림·부정문이 아닐 때만 근거가 된다. 버린 것은 사유와 함께 rejected 에 남긴다.
"""

from __future__ import annotations

import re
from typing import Literal

from ai.case.lexicon import INSTITUTION_PATTERN
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
_TRAILING_PUNCT = re.compile(r"[.!\s]+$")

_CLAUSE_BREAK = re.compile(r"(?:고(?=\s+(?!있|계시|싶))|며|지만|는데|,)\s+")
_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://")


class VerifyResult(StrictModel):
    accepted: list[Evidence]
    rejected: list[RejectedClaim]
    other_speaker: str | None
    speaker_split_failed: bool
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
    # "입력하시면 됩니다" 는 요구라서 통과한다
    return bool(m) and not sentence[: m.start()].rstrip().endswith("면")


def _last_clause_start(quote: str) -> int:
    ends = [m.end() for m in _CLAUSE_BREAK.finditer(quote)]
    return ends[-1] if ends else 0


def _verify_host(raw: str) -> str | None:
    host = _SCHEME.sub("", raw.strip().lower())
    host = re.split(r"[/?#]", host, maxsplit=1)[0]
    return host if _HOST.match(host) else None


def verify_call(
    source: ReadingSource,
    reading: ClaimReading,
    *,
    derived_by: Literal["llm", "lexicon"],
) -> VerifyResult:
    segments = [(seg, _norm(seg.text)) for seg in source.segments]
    names = {seg.speaker for seg in source.segments}

    other = reading.other_speaker or source.other_speaker
    if other not in names:
        other = None
    # V-8
    split_failed = (
        source.speaker_split_failed
        or reading.speaker_confidence == "low"
        or other is None
    )

    def allowed(seg_speaker: str | None) -> bool:
        return split_failed or seg_speaker == other

    accepted: list[Evidence] = []
    rejected: list[RejectedClaim] = []

    def locate(kind: str, quote: str):
        """V-1·V-2. 통과하면 (인용, 세그먼트 텍스트, 인용 끝 위치), 아니면 None."""
        q = _norm(quote)
        if len(q.replace(" ", "")) < MIN_QUOTE_CHARS:
            rejected.append(RejectedClaim(kind=kind, quote=quote, reason="too_short"))
            return None
        found = [(seg, text) for seg, text in segments if q in text]
        if not found:
            rejected.append(RejectedClaim(kind=kind, quote=quote, reason="not_in_source"))
            return None
        for seg, text in found:
            if allowed(seg.speaker):
                return q, text, text.index(q) + len(q)
        rejected.append(RejectedClaim(kind=kind, quote=quote, reason="self_side"))
        return None

    def add(code: EvidenceCode, quote: str) -> None:
        accepted.append(
            Evidence(
                code=code,
                origin=Origin.TRANSCRIPT,
                quote=quote,
                derived_by=derived_by,
                counts_for_floor=code in _FLOOR_CODES,
            )
        )

    claims = ClaimView()

    for ident in reading.identity:
        hit = locate("identity", ident.quote)
        if hit is None:
            continue
        q = hit[0]
        listed = False
        if ident.kind == IdentityKind.INSTITUTION:
            listed = bool(
                INSTITUTION_PATTERN.search(ident.name) or INSTITUTION_PATTERN.search(q)
            )
            if listed:
                add(EvidenceCode.INSTITUTION_CLAIM, q)
        elif ident.kind == IdentityKind.ACQUAINTANCE:
            add(EvidenceCode.ACQUAINTANCE_CLAIM, q)
        claims.identity.append(
            ClaimViewIdentity(
                kind=ident.kind, name=ident.name, quote=q, listed_institution=listed
            )
        )

    for pretext in reading.pretexts:
        hit = locate("pretext", pretext.quote)
        if hit is not None:
            claims.pretexts.append(hit[0])

    for demand in reading.demands:
        hit = locate("demand", demand.quote)
        if hit is None:
            continue
        q, text, end = hit
        if _is_notice_sentence(q):
            rejected.append(
                RejectedClaim(kind="demand", quote=demand.quote, reason="notice_sentence")
            )
            continue
        # 인용이 부정문 전체인 경우가 많아 마지막 절 안의 어느 낱말 뒤에서든 본다.
        # 앞 절의 부정("걱정하지 않으셔도 되고")이 뒤 절의 요구를 지우면 안 된다
        start = end - len(q) + _last_clause_start(q)
        if any(action_is_denied(text, pos) for pos in range(start, end + 1)):
            rejected.append(
                RejectedClaim(kind="demand", quote=demand.quote, reason="negated")
            )
            continue
        code = _DEMAND_CODE.get(demand.kind)
        if code is not None:
            add(code, q)
        claims.demands.append(ClaimViewDemand(kind=demand.kind, quote=q))

    for blocker in reading.blockers:
        hit = locate("blocker", blocker.quote)
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
        other_speaker=other,
        speaker_split_failed=split_failed,
        spoken_hosts=hosts,
        claims=claims,
    )
