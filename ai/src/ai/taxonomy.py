"""현재 문자 원문에 근거한 브랜드, 용건, 요청 분류."""

from __future__ import annotations

from dataclasses import dataclass
import re

from ai.kb.normalize import normalize
from ai.message_requests import action_is_denied, normalized_with_positions, request_context
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
# 택배 문자에서만 택배사로 읽는 짧은 이름. "우체국"·"롯데"만으로는 예금·보험·카드
# 같은 같은 회사 이름의 다른 업무와 구분되지 않는다. 정규화한 본문에 대조한다.
_PARCEL_SHORT_ALIASES: dict[Brand, re.Pattern[str]] = {
    Brand.EPOST: re.compile(r"우체국(?!예금|보험)"),
    Brand.HANJIN: re.compile(r"한진(?!해운|관광|칼)"),
    Brand.LOTTE_PARCEL: re.compile(
        r"롯데(?!카드|몰|홈쇼핑|마트|온|on|백화점|손해보험|캐피탈)"
    ),
    Brand.LOGEN: re.compile(r"로젠"),
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
    if not brands and classify_topic(text, extracted) is Topic.PARCEL:
        # 정식 이름으로 알아본 브랜드는 바꾸지 않고, 못 알아봤을 때만 짧은 이름을 본다.
        brands = {
            brand
            for brand, pattern in _PARCEL_SHORT_ALIASES.items()
            if pattern.search(normalized)
        }
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
_PENALTY_TERMS = (
    "과태료", "범칙금", "무단투기", "무단투입", "불법투기", "불법배출", "혼합배출", "혼합투기",
    "주정차위반", "주정차단속", "불법주정차", "무인단속", "교통법규위반",
)
_WASTE_TERMS = ("폐기물", "쓰레기", "음식물", "종량제")
_VIOLATION_TERMS = ("위반", "부과", "무단", "불법", "투기")
# "[국외발신]" 은 normalize 가 지우지만 괄호 없는 "국외발신" 과 "해외배송" 은 남는다.
_OVERSEAS_RE = re.compile(r"(?:해외|국외)(?!배송|발신)")
_OVERSEAS_PAYMENT_TERMS = ("결제", "승인", "카드", "이상거래")
# 과태료·결제 사칭은 주소·반송·결제 완료·계좌 같은 다른 주제의 단어를 명분으로 섞어 쓴다.
_PENALTY_CONTEXT = frozenset(
    {Topic.PENALTY, Topic.PAYMENT, Topic.PARCEL, Topic.SHOPPING, Topic.FINANCE, Topic.PUBLIC}
)
_PAYMENT_CONTEXT = frozenset({Topic.PAYMENT, Topic.SHOPPING, Topic.PARCEL, Topic.FINANCE})


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
    if _contains_any(normalized, _PENALTY_TERMS) or (
        _contains_any(normalized, _WASTE_TERMS)
        and _contains_any(normalized, _VIOLATION_TERMS)
    ):
        topics.add(Topic.PENALTY)
    if ("카드" in normalized and "승인" in normalized) or (
        _OVERSEAS_RE.search(normalized)
        and _contains_any(normalized, _OVERSEAS_PAYMENT_TERMS)
    ):
        topics.add(Topic.PAYMENT)

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

    # 검진 안내는 "미수검 시 과태료" 를 덧붙여 쓴다. 과태료가 핵심 용건이 아니다.
    if Topic.HEALTH in topics and topics <= {Topic.HEALTH, Topic.PENALTY}:
        return Topic.HEALTH
    if Topic.PENALTY in topics and topics <= _PENALTY_CONTEXT:
        return Topic.PENALTY
    if Topic.PAYMENT in topics and topics <= _PAYMENT_CONTEXT:
        return Topic.PAYMENT
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


def _find_action_candidates(text: str) -> list[MessageCandidate]:
    context = request_context(text)
    normalized, positions = normalized_with_positions(context)
    candidates: list[MessageCandidate] = []
    for doubt, pattern in _ACTION_RULES:
        for match in pattern.finditer(normalized):
            start = positions[match.start()].start
            end = positions[match.end() - 1].end
            if action_is_denied(context, end):
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
        normalized_evidence = normalize(action.evidence)
        # A known but prohibited/reported action must not return as UNKNOWN.
        if any(pattern.search(normalized_evidence) for _, pattern in _ACTION_RULES):
            continue
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


def select_message_doubt(candidates: list[MessageCandidate]) -> MessageDoubt | None:
    """포함 관계를 정리한 후 원문 순서로 대표값을 선택한다. 후보가 없으면 None."""
    filtered = filter_message_candidates(candidates)
    return filtered[0].doubt if filtered else None


def filter_message_candidates(candidates: list[MessageCandidate]) -> list[MessageCandidate]:
    """포함 관계로 가려진 후보를 지우고 원문 순서로 정렬한다."""

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
        if candidate.doubt is MessageDoubt.UNKNOWN and any(
            item.doubt is not MessageDoubt.UNKNOWN for item in candidates
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
    return sorted(filtered, key=lambda item: (item.start, -len(item.evidence)))
