"""현재 문자 원문에 근거한 브랜드, 용건, 요청 분류."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re

from ai.kb.normalize import normalize
from ai.types import AnalysisStatus, Brand, MessageAnalysis, MessageDoubt, Topic


@dataclass(frozen=True)
class MessageCandidate:
    doubt: MessageDoubt
    evidence: str
    start: int


_BRAND_ALIASES: dict[Brand, tuple[str, ...]] = {
    Brand.CJ_LOGISTICS: ("CJ대한통운",),
    Brand.CJ_PARCEL: ("CJ택배",),
    Brand.CJ_EXPRESS: ("CJ익스프레스",),
    Brand.CJ_SHOPPING: ("CJ오쇼핑",),
    Brand.HANJIN: ("한진택배",),
    Brand.LOGEN: ("로젠택배",),
    Brand.EPOST: ("우체국택배", "우체국물류"),
    Brand.DHL: ("DHL",),
    Brand.HYUNDAI: ("현대택배",),
    Brand.LOTTE_PARCEL: ("롯데택배",),
    Brand.CU: ("CU물류", "CU택배", "CU편의점"),
    Brand.DAESHIN: ("대신택배",),
    Brand.KGB: ("KGB택배",),
    Brand.KYUNGDONG: ("경동택배",),
    Brand.HAPDONG: ("합동택배",),
    Brand.COUPANG: ("쿠팡",),
    Brand.AUCTION: ("옥션",),
    Brand.LOTTE_MALL: ("롯데몰",),
    Brand.KAKAO_GIFT: ("카카오톡선물하기",),
    Brand.SEVEN_ELEVEN: ("7-11",),
    Brand.RAKUTEN_EXPRESS: ("라쿠텐익스프레스",),
    Brand.KISA: ("KISA",),
    Brand.PROSECUTION: ("검찰청", "사이버검찰청"),
}

_NORMALIZED_BRAND_ALIASES = {
    brand: tuple(normalize(alias) for alias in aliases)
    for brand, aliases in _BRAND_ALIASES.items()
}
_STANDALONE_CU_RE = re.compile(r"(?<![0-9a-z])cu(?![0-9a-z])", re.IGNORECASE)
_NON_BRANDS = tuple(
    normalize(value)
    for value in (
        "CA대한통운",
        "CZ대한통운",
        "CG대한통운",
        "CX대한통운",
        "CS대한통운",
        "대신대한통운",
        "CJ우체국",
    )
)


def _brands_in(text: str) -> set[Brand]:
    normalized = normalize(text)
    brands = {
        brand
        for brand, aliases in _NORMALIZED_BRAND_ALIASES.items()
        if any(alias in normalized for alias in aliases)
    }
    if _STANDALONE_CU_RE.search(text):
        brands.add(Brand.CU)
    return brands


def identify_brand(text: str, extracted: MessageAnalysis | None = None) -> Brand:
    """현재 입력에서 명시된 브랜드만 선택하며 혼합·변형 명칭은 교정하지 않는다."""

    normalized = normalize(text)
    if any(non_brand in normalized for non_brand in _NON_BRANDS):
        return Brand.UNKNOWN

    brands = _brands_in(text)
    if not brands:
        return Brand.UNKNOWN
    if len(brands) == 1:
        return next(iter(brands))

    if extracted is not None:
        claim = extracted.claimed_sender
        if claim.evidence and claim.evidence in text:
            evidence_brands = _brands_in(claim.evidence)
            value_brands = _brands_in(claim.value or "")
            if len(evidence_brands) == 1 and (
                not value_brands or value_brands == evidence_brands
            ):
                claimed = next(iter(evidence_brands))
                if claimed in brands:
                    return claimed
    return Brand.UNKNOWN


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


_TOPIC_CLAUSE_SPLIT_RE = re.compile(r"[.!?\n。！？]")
_PAYMENT_INSTRUMENT_RE = re.compile(r"(?:은행카드|카드|(?:은행)?계좌)로")
_PARCEL_REQUEST_RE = re.compile(r"(?:택배|배송|운송)(?:상태|현황)?(?:을|를)?(?:조회|확인)")
_SHOPPING_CANCEL_RE = re.compile(r"(?:주문|구매)?취소")
_PURPOSE_CONNECTOR_RE = re.compile(r"(?:요청|안내)?(?:와|과|및)(?:별도)?")


def _topic_clauses(text: str) -> list[str]:
    return [
        normalized
        for clause in _TOPIC_CLAUSE_SPLIT_RE.split(text)
        if (normalized := normalize(clause))
    ]


def _has_payment_instrument_shopping_request(text: str) -> bool:
    for clause in _topic_clauses(text):
        if (
            _PAYMENT_INSTRUMENT_RE.search(clause)
            and _contains_any(clause, ("결제", "구매"))
            and _contains_any(clause, ("주문", "상품", "구매"))
            and _contains_any(clause, ("취소", "환불"))
        ):
            return True
    return False


def _has_independent_parcel_and_shopping_requests(text: str) -> bool:
    for clause in _topic_clauses(text):
        parcel_matches = tuple(_PARCEL_REQUEST_RE.finditer(clause))
        shopping_matches = tuple(_SHOPPING_CANCEL_RE.finditer(clause))
        for parcel in parcel_matches:
            for shopping in shopping_matches:
                first, second = sorted((parcel, shopping), key=lambda item: item.start())
                between = clause[first.end() : second.start()]
                if _PURPOSE_CONNECTOR_RE.fullmatch(between):
                    return True
    return False


def classify_topic(text: str, extracted: MessageAnalysis | None = None) -> Topic:
    """현재 입력의 핵심 용건과 검증된 기존 추출 결과를 사용한다."""

    normalized = normalize(text)
    topics: set[Topic] = set()

    if _contains_any(normalized, ("정부지원", "정부보조금", "보조금")) or (
        "검찰" in normalized and _contains_any(normalized, ("사건", "처리"))
    ):
        topics.add(Topic.PUBLIC)
    if _contains_any(normalized, ("건강검진", "검진", "감염", "접촉안내")):
        topics.add(Topic.HEALTH)
    if _contains_any(normalized, ("보안공지", "보안안내", "계정보안")):
        topics.add(Topic.SECURITY)
    if _contains_any(normalized, ("은행", "급여", "계좌", "증권", "인출")):
        topics.add(Topic.FINANCE)

    cancel_or_refund = _contains_any(normalized, ("취소", "환불"))
    shopping = cancel_or_refund or _contains_any(
        normalized,
        ("주문", "구매", "결제성공", "결제완료"),
    )
    parcel = _contains_any(
        normalized,
        ("택배", "배송", "운송", "반송", "배달", "배송사진", "주소변경", "주소확인"),
    )
    gift = _contains_any(
        normalized,
        ("당첨", "상품권", "선물교환", "교환내역", "선물하기"),
    )
    if shopping:
        topics.add(Topic.SHOPPING)
    if parcel:
        topics.add(Topic.PARCEL)
    if gift:
        topics.add(Topic.GIFT)

    if (
        {Topic.PARCEL, Topic.SHOPPING} <= topics
        and _has_independent_parcel_and_shopping_requests(text)
    ):
        return Topic.UNKNOWN
    if Topic.PUBLIC in topics and topics <= {Topic.PUBLIC, Topic.FINANCE}:
        return Topic.PUBLIC
    shopping_context = {Topic.SHOPPING, Topic.PARCEL, Topic.GIFT}
    if _has_payment_instrument_shopping_request(text):
        shopping_context.add(Topic.FINANCE)
    if cancel_or_refund and topics <= shopping_context:
        return Topic.SHOPPING
    if Topic.PARCEL in topics and topics <= {Topic.PARCEL, Topic.SHOPPING, Topic.GIFT}:
        return Topic.PARCEL
    if len(topics) == 1:
        return next(iter(topics))
    if extracted is not None:
        purpose = extracted.claimed_purpose
        if purpose.evidence and purpose.evidence in text:
            grounded_topic = classify_topic(purpose.evidence)
            if grounded_topic in topics:
                return grounded_topic
    return Topic.UNKNOWN


@dataclass(frozen=True)
class _SourceChar:
    value: str
    start: int
    end: int


_ENTITY_RE = re.compile(r"&(?:#[xX][0-9a-fA-F]+|#\d+|[a-zA-Z][a-zA-Z0-9]+);")
_KEEP_CHAR_RE = re.compile(r"[0-9a-z가-힣]", re.IGNORECASE)


def _normalized_with_positions(text: str) -> tuple[str, list[_SourceChar]]:
    decoded: list[_SourceChar] = []
    index = 0
    while index < len(text):
        lowered = text[index : index + 3].lower()
        if lowered in {"&l;", "&g;"}:
            index += 3
            continue

        entity = _ENTITY_RE.match(text, index)
        if entity is not None:
            raw = entity.group(0)
            value = html.unescape(raw)
            if value != raw:
                decoded.extend(
                    _SourceChar(char, index, entity.end()) for char in value
                )
                index = entity.end()
                continue

        if text[index : index + 4].lower() == "amp;":
            index += 4
            continue
        decoded.append(_SourceChar(text[index], index, index + 1))
        index += 1

    kept = [item for item in decoded if _KEEP_CHAR_RE.fullmatch(item.value)]
    return "".join(item.value.lower() for item in kept), kept


_REQUEST = (
    r"(?:부탁(?:드립니다|합니다)?|해?주세요|하세요|하십시오|으십시오|"
    r"하시길바랍니다|바랍니다)"
)
_OPTIONAL_REQUEST = rf"(?:{_REQUEST})?"
_ACTION_RULES: tuple[tuple[MessageDoubt, re.Pattern[str]], ...] = (
    (
        MessageDoubt.DATA_INPUT,
        re.compile(
            rf"(?:본인확인을위해)?(?:고객)?번호(?:를)?입력{_REQUEST}"
        ),
    ),
    (
        MessageDoubt.DATA_INPUT,
        re.compile(rf"정보(?:를)?입력{_REQUEST}"),
    ),
    (
        MessageDoubt.APP_INSTALL,
        re.compile(r"앱(?:을)?(?:다운로드|설치)"),
    ),
    (
        MessageDoubt.ADDRESS_EDIT,
        re.compile(rf"주소(?:지)?(?:를)?(?:입력|변경|수정|정정){_REQUEST}"),
    ),
    (
        MessageDoubt.ADDRESS_CHECK,
        re.compile(rf"주소(?:지)?(?:를)?확인(?:{_REQUEST}|후)"),
    ),
    (
        MessageDoubt.IDENTITY_CHECK,
        re.compile(rf"본인확인{_REQUEST}"),
    ),
    (
        MessageDoubt.PHOTO_VIEW,
        re.compile(rf"(?:클릭하여)?사진(?:보기|확인){_OPTIONAL_REQUEST}"),
    ),
    (
        MessageDoubt.PARCEL_LOOKUP,
        re.compile(rf"{_PARCEL_REQUEST_RE.pattern}{_OPTIONAL_REQUEST}"),
    ),
    (
        MessageDoubt.DETAIL_VIEW,
        re.compile(
            rf"(?:클릭하여)?(?:상세내용|거래내역|교환내역|공지|안내|내용)(?:을)?"
            rf"(?:확인|조회){_OPTIONAL_REQUEST}"
        ),
    ),
    (
        MessageDoubt.CANCEL_REFUND,
        re.compile(
            rf"(?:(?:주문|접수|구매)?취소(?:처리|진행)?(?:를)?{_REQUEST}|"
            rf"환불(?:을원하시면클릭하세요|(?:진행|신청)(?:을)?{_REQUEST}))"
        ),
    ),
    (
        MessageDoubt.PICKUP,
        re.compile(
            rf"(?:직접수령{_REQUEST}|수령(?:시간|일정)(?:을)?확인{_REQUEST}|"
            rf"픽업{_REQUEST})"
        ),
    ),
    (
        MessageDoubt.WITHDRAW,
        re.compile(rf"인출{_REQUEST}"),
    ),
    (
        MessageDoubt.ANSWER_PHONE,
        re.compile(rf"전화(?:를)?받{_REQUEST}"),
    ),
    (
        MessageDoubt.OPEN_LINK,
        re.compile(
            rf"(?:(?:아래)?(?:url|링크)(?:을|를)?(?:클릭|접속)"
            rf"{_OPTIONAL_REQUEST}|클릭{_OPTIONAL_REQUEST})"
        ),
    ),
)


def _make_candidate(
    text: str, doubt: MessageDoubt, evidence: str
) -> MessageCandidate:
    if not evidence.strip() or evidence not in text:
        raise ValueError("candidate evidence is not in the current message")
    return MessageCandidate(doubt=doubt, evidence=evidence, start=text.index(evidence))


_CLAUSE_END_RE = re.compile(r"[.!?\n。！？]")
_COMPLETION_TAIL_RE = re.compile(
    r"^(?:(?:처리|작업)(?:[이가을를])?)?"
    r"(?:(?:정상적으로|성공적으로|모두))?(?:[이가을를])?"
    r"완료(?:$|입니다|되었|됐|했|하였|됨|(?:안내|알림)(?:입니다)?)"
)


def _is_completion_notice(text: str, action_end: int) -> bool:
    clause_end = _CLAUSE_END_RE.search(text, action_end)
    tail_end = clause_end.start() if clause_end is not None else len(text)
    return _COMPLETION_TAIL_RE.match(normalize(text[action_end:tail_end])) is not None


def _find_action_candidates(text: str) -> list[MessageCandidate]:
    normalized, positions = _normalized_with_positions(text)
    candidates: list[MessageCandidate] = []
    for doubt, pattern in _ACTION_RULES:
        for match in pattern.finditer(normalized):
            start = positions[match.start()].start
            end = positions[match.end() - 1].end
            if _is_completion_notice(text, end):
                continue
            evidence = text[start:end]
            if evidence not in text:
                continue
            candidates.append(MessageCandidate(doubt, evidence, start))
    return candidates


def message_candidates(
    text: str, extracted: MessageAnalysis
) -> list[MessageCandidate]:
    """실제 원문과 위치를 보존한 요청 후보를 반환한다."""

    candidates = _find_action_candidates(text)
    for action in extracted.requested_actions:
        if not action.evidence or action.evidence not in text:
            continue
        if _find_action_candidates(action.evidence):
            continue
        normalized_evidence = normalize(action.evidence)
        if _contains_any(
            normalized_evidence,
            ("부탁", "해주세요", "하십시오", "하세요", "요청"),
        ):
            candidates.append(
                _make_candidate(text, MessageDoubt.UNKNOWN, action.evidence)
            )

    unique: dict[tuple[MessageDoubt, int], MessageCandidate] = {}
    for candidate in candidates:
        key = candidate.doubt, candidate.start
        previous = unique.get(key)
        if previous is None or len(candidate.evidence) > len(previous.evidence):
            unique[key] = candidate
    result = sorted(unique.values(), key=lambda item: (item.start, -len(item.evidence)))

    if not result and extracted.analysis_status is AnalysisStatus.FALLBACK:
        if text:
            return [_make_candidate(text, MessageDoubt.UNKNOWN, text)]
        return [MessageCandidate(MessageDoubt.UNKNOWN, "", 0)]
    return result


def _overlaps(left: MessageCandidate, right: MessageCandidate) -> bool:
    left_end = left.start + len(left.evidence)
    right_end = right.start + len(right.evidence)
    return left.start < right_end and right.start < left_end


def _first_candidate(candidates: list[MessageCandidate]) -> MessageDoubt:
    if not candidates:
        return MessageDoubt.NONE
    return min(candidates, key=lambda item: (item.start, -len(item.evidence))).doubt


def select_message_doubt(candidates: list[MessageCandidate]) -> MessageDoubt:
    """포함 관계를 정리한 후 원문 순서로 대표값을 선택한다."""

    concrete = {
        MessageDoubt.APP_INSTALL,
        MessageDoubt.ADDRESS_EDIT,
        MessageDoubt.ADDRESS_CHECK,
        MessageDoubt.IDENTITY_CHECK,
        MessageDoubt.DATA_INPUT,
        MessageDoubt.PHOTO_VIEW,
        MessageDoubt.PARCEL_LOOKUP,
        MessageDoubt.DETAIL_VIEW,
        MessageDoubt.CANCEL_REFUND,
        MessageDoubt.PICKUP,
        MessageDoubt.WITHDRAW,
        MessageDoubt.ANSWER_PHONE,
    }
    suppressions = {
        MessageDoubt.ADDRESS_EDIT: {
            MessageDoubt.ADDRESS_CHECK,
            MessageDoubt.DATA_INPUT,
            MessageDoubt.DETAIL_VIEW,
            MessageDoubt.OPEN_LINK,
        },
        MessageDoubt.DATA_INPUT: {
            MessageDoubt.IDENTITY_CHECK,
            MessageDoubt.DETAIL_VIEW,
            MessageDoubt.OPEN_LINK,
        },
    }

    filtered: list[MessageCandidate] = []
    for candidate in candidates:
        if candidate.doubt in {MessageDoubt.NONE, MessageDoubt.UNKNOWN} and any(
            item.doubt not in {MessageDoubt.NONE, MessageDoubt.UNKNOWN}
            for item in candidates
        ):
            continue
        suppressed = False
        for other in candidates:
            if other is candidate or not _overlaps(candidate, other):
                continue
            if candidate.doubt in suppressions.get(other.doubt, set()):
                suppressed = True
                break
            if (
                other.doubt in concrete
                and candidate.doubt
                in {MessageDoubt.DETAIL_VIEW, MessageDoubt.OPEN_LINK}
            ):
                suppressed = True
                break
        if not suppressed:
            filtered.append(candidate)
    return _first_candidate(filtered)
