import pytest

from ai.taxonomy import (
    MessageCandidate,
    classify_topic,
    identify_brand,
    message_candidates,
    select_message_doubt,
)
from ai.types import (
    AnalysisStatus,
    Brand,
    CategoryCode,
    CategoryEvidence,
    EvidenceField,
    MessageAnalysis,
    MessageDoubt,
    Topic,
)


def analysis(
    *,
    status: AnalysisStatus = AnalysisStatus.COMPLETED,
    requested_actions: list[EvidenceField] | None = None,
    claimed_sender: EvidenceField | None = None,
    categories: list[CategoryEvidence] | None = None,
) -> MessageAnalysis:
    return MessageAnalysis(
        analysis_status=status,
        requested_actions=requested_actions or [],
        claimed_sender=claimed_sender or EvidenceField(),
        categories=categories or [],
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[C J대한통운] 배송지 주소를 확인하세요", Brand.CJ_LOGISTICS),
        ("[CJ오쇼핑] 주소 변경 부탁드립니다", Brand.CJ_SHOPPING),
        ("CA대한통운 택배가 도착했습니다", Brand.UNKNOWN),
        ("CZ대한통운 택배가 도착했습니다", Brand.UNKNOWN),
        ("CG대한통운 택배가 도착했습니다", Brand.UNKNOWN),
        ("CX대한통운 택배가 도착했습니다", Brand.UNKNOWN),
        ("C S 대한 통운을 통해 발송되었습니다", Brand.UNKNOWN),
        ("CJ 우체국을 통해 물품이 발송되었습니다", Brand.UNKNOWN),
        ("[대신대한통운] 택배가 도착했습니다", Brand.UNKNOWN),
    ],
)
def test_brand_preserves_claim_without_fuzzy_correction(text, expected):
    assert identify_brand(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C.J택배 배송 조회", Brand.CJ_PARCEL),
        ("C·J 익·스·프·레·스 배송", Brand.CJ_EXPRESS),
        ("한진택 배 보관 완료", Brand.HANJIN),
        ("로젠택 배 배송 확인", Brand.LOGEN),
        ("우-체-국-택-배 배송", Brand.EPOST),
        ("dhl 배송 안내", Brand.DHL),
        ("현대택배 배송 안내", Brand.HYUNDAI),
        ("롯데택배 배송 안내", Brand.LOTTE_PARCEL),
        ("cu편의점 택배 안내", Brand.CU),
        ("대.신.택배 배송 안내", Brand.DAESHIN),
        ("K G B택 배 배송 안내", Brand.KGB),
        ("경 동 택 배 배송 안내", Brand.KYUNGDONG),
        ("합.동택.배 배송 안내", Brand.HAPDONG),
        ("쿠팡 주문 안내", Brand.COUPANG),
        ("옥션 주문 취소", Brand.AUCTION),
        ("롯데 몰 주문 안내", Brand.LOTTE_MALL),
        ("카카오톡 선물하기 교환내역 조회", Brand.KAKAO_GIFT),
        ("7-11 택배 안내", Brand.SEVEN_ELEVEN),
        ("라쿠텐 익스프레스 배송 안내", Brand.RAKUTEN_EXPRESS),
        ("[KISA 보안공지] 중요공지사항", Brand.KISA),
        ("사이버 검찰청 사건 처리 안내", Brand.PROSECUTION),
    ],
)
def test_brand_uses_only_documented_aliases(text, expected):
    assert identify_brand(text) is expected


def test_brand_uses_grounded_claimed_sender_to_resolve_multiple_names():
    text = "[CJ오쇼핑] 주문 상품은 한진택배로 배송됩니다"
    extracted = analysis(
        claimed_sender=EvidenceField(value="CJ오쇼핑", evidence="CJ오쇼핑")
    )

    assert identify_brand(text, extracted) is Brand.CJ_SHOPPING


def test_brand_ignores_claimed_sender_without_current_message_evidence():
    text = "한진택배 배송 안내"
    extracted = analysis(
        claimed_sender=EvidenceField(value="CJ대한통운", evidence="CJ대한통운")
    )

    assert identify_brand(text, extracted) is Brand.HANJIN


def test_brand_returns_unknown_for_unresolved_multiple_names():
    assert identify_brand("CJ대한통운과 한진택배 배송 안내") is Brand.UNKNOWN


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("정부 보조금이 은행 카드로 송금되었습니다. 확인하세요.", Topic.PUBLIC),
        ("[CJ오쇼핑] 택배 주소가 모호해 주소 변경 부탁드립니다", Topic.PARCEL),
        ("온라인 상품 구매에 대한 결제 성공", Topic.SHOPPING),
        ("[KISA 보안공지] 중요공지사항", Topic.SECURITY),
        ("선물이 배송되었습니다", Topic.PARCEL),
        ("카카오톡 선물하기 교환내역 조회", Topic.GIFT),
        ("상품은 1일 후 배송됩니다. 환불을 원하시면 클릭하세요.", Topic.SHOPPING),
        ("이번 달 급여가 지급되었습니다. 제때 인출해주세요.", Topic.FINANCE),
        ("은행 대출 안내", Topic.FINANCE),
        ("건강 검진 결과를 확인하세요", Topic.HEALTH),
    ],
)
def test_topic_uses_purpose_not_brand(text, expected):
    assert classify_topic(text) is expected


def test_topic_does_not_turn_kisa_name_alone_into_security():
    assert classify_topic("KISA에서 알려드립니다") is Topic.UNKNOWN


def test_topic_returns_unknown_for_independent_equal_purposes():
    assert classify_topic("급여 계좌 안내와 건강 검진 안내") is Topic.UNKNOWN


def test_topic_uses_grounded_claimed_purpose_to_resolve_equal_purposes():
    text = "급여 계좌 안내와 건강 검진 안내"
    extracted = MessageAnalysis(
        analysis_status=AnalysisStatus.COMPLETED,
        claimed_purpose=EvidenceField(value="검진 안내", evidence="건강 검진 안내"),
    )

    assert classify_topic(text, extracted) is Topic.HEALTH


def test_topic_ignores_ungrounded_legacy_category():
    extracted = analysis(
        categories=[
            CategoryEvidence(
                code=CategoryCode.PUBLIC_SUPPORT,
                evidence="정부 지원금",
            )
        ]
    )

    assert classify_topic("새 소식이 있습니다", extracted) is Topic.UNKNOWN


def test_topic_does_not_map_legacy_code_over_grounded_text_meaning():
    text = "택배 배송 안내"
    extracted = analysis(
        categories=[
            CategoryEvidence(code=CategoryCode.PAYMENT, evidence="택배 배송")
        ]
    )

    assert classify_topic(text, extracted) is Topic.PARCEL


def test_ce_0114_keeps_malformed_delimiters_and_source_order():
    text = (
        "[Web발신] [CJ대한통운]배송불가&l;도로명불일치&g;"
        "앱 다운로드 주소지확인 부탁드립니다"
    )
    extracted = analysis(
        requested_actions=[
            EvidenceField(value="앱 설치", evidence="앱 다운로드"),
            EvidenceField(value="주소 확인", evidence="주소지확인 부탁드립니다"),
        ]
    )

    candidates = message_candidates(text, extracted)

    assert identify_brand(text, extracted) is Brand.CJ_LOGISTICS
    assert classify_topic(text, extracted) is Topic.PARCEL
    assert select_message_doubt(candidates) is MessageDoubt.APP_INSTALL
    assert any(item.doubt is MessageDoubt.ADDRESS_CHECK for item in candidates)
    assert all(item.evidence in text for item in candidates)
    assert all(text[item.start : item.start + len(item.evidence)] == item.evidence for item in candidates)


def test_ce_0286_keeps_unspecified_number_as_generic_data_input():
    text = "(익스프레스 반품 처리 중) 본인 확인을 위해 번호를 입력 해주세요."
    extracted = analysis(
        requested_actions=[
            EvidenceField(
                value="비밀번호 입력",
                evidence="본인 확인을 위해 번호를 입력 해주세요",
            )
        ]
    )

    candidates = message_candidates(text, extracted)

    assert select_message_doubt(candidates) is MessageDoubt.DATA_INPUT
    assert any(
        item.doubt is MessageDoubt.DATA_INPUT
        and item.evidence == "본인 확인을 위해 번호를 입력 해주세요"
        for item in candidates
    )
    assert all("비밀번호" not in item.evidence for item in candidates)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("앱을 설치해주세요", MessageDoubt.APP_INSTALL),
        ("배송 주소를 변경 부탁드립니다", MessageDoubt.ADDRESS_EDIT),
        ("배송 주소를 확인해주세요", MessageDoubt.ADDRESS_CHECK),
        ("본인 확인 부탁드립니다", MessageDoubt.IDENTITY_CHECK),
        ("고객 번호를 입력해주세요", MessageDoubt.DATA_INPUT),
        ("문 앞 배송 사진 보기", MessageDoubt.PHOTO_VIEW),
        ("택배 배송 조회", MessageDoubt.PARCEL_LOOKUP),
        ("공지 상세 내용을 확인해주세요", MessageDoubt.DETAIL_VIEW),
        ("주문 취소 처리를 부탁드립니다", MessageDoubt.CANCEL_REFUND),
        ("직접 수령하시길 바랍니다", MessageDoubt.PICKUP),
        ("제때 인출해주세요", MessageDoubt.WITHDRAW),
        ("전화를 받으십시오", MessageDoubt.ANSWER_PHONE),
        ("아래 URL을 클릭해주세요", MessageDoubt.OPEN_LINK),
    ],
)
def test_message_action_taxonomy_uses_explicit_request_patterns(text, expected):
    assert select_message_doubt(message_candidates(text, analysis())) is expected


def test_install_and_address_keep_source_order_and_all_evidence():
    text = "앱 다운로드 후 주소 확인 부탁드립니다"
    extracted = analysis(
        requested_actions=[
            EvidenceField(value="앱 설치", evidence="앱 다운로드"),
            EvidenceField(value="주소 확인", evidence="주소 확인"),
        ]
    )

    candidates = message_candidates(text, extracted)

    assert select_message_doubt(candidates) is MessageDoubt.APP_INSTALL
    assert any(item.doubt is MessageDoubt.ADDRESS_CHECK for item in candidates)
    assert all(item.evidence in text for item in candidates)


def test_independent_requests_use_original_order_instead_of_label_priority():
    text = "주소 확인 후 앱 다운로드 부탁드립니다"
    candidates = message_candidates(text, analysis())

    assert select_message_doubt(candidates) is MessageDoubt.ADDRESS_CHECK
    assert any(item.doubt is MessageDoubt.APP_INSTALL for item in candidates)


@pytest.mark.parametrize(
    "text",
    [
        "온라인 상품 구매에 대한 결제 성공",
        "대리 결제 상품이 배송되었습니다",
        "요청하신 환불이 완료되었습니다",
        "주문 취소 처리가 완료되었습니다",
        "주소 변경이 완료되었습니다",
        "본인 확인이 완료되었습니다",
        "계좌 인출이 완료되었습니다",
        "택배가 문 앞에 배송되었습니다",
    ],
)
def test_completion_and_plain_notice_are_not_action_requests(text):
    assert select_message_doubt(message_candidates(text, analysis())) is MessageDoubt.NONE


def test_requested_action_value_is_not_accepted_without_semantic_evidence():
    text = "온라인 상품 구매에 대한 결제 성공"
    extracted = analysis(
        requested_actions=[
            EvidenceField(value="앱 설치", evidence="결제 성공"),
        ]
    )

    assert select_message_doubt(message_candidates(text, extracted)) is MessageDoubt.NONE


def test_requested_action_evidence_must_exist_in_current_message():
    text = "택배가 문 앞에 배송되었습니다"
    extracted = analysis(
        requested_actions=[
            EvidenceField(value="앱 설치", evidence="앱 다운로드"),
        ]
    )

    assert message_candidates(text, extracted) == []


def test_grounded_but_unmapped_extracted_request_is_unknown():
    text = "별도 처리를 부탁드립니다"
    extracted = analysis(
        requested_actions=[
            EvidenceField(value="처리", evidence="처리를 부탁드립니다"),
        ]
    )

    assert select_message_doubt(message_candidates(text, extracted)) is MessageDoubt.UNKNOWN


def test_failed_analysis_without_verified_candidate_is_unknown():
    text = "택배 관련 안내입니다"
    extracted = analysis(status=AnalysisStatus.FALLBACK)

    candidates = message_candidates(text, extracted)

    assert select_message_doubt(candidates) is MessageDoubt.UNKNOWN
    assert candidates[0].evidence == text


def test_completed_analysis_without_request_is_none():
    text = "택배 관련 안내입니다"

    assert select_message_doubt(message_candidates(text, analysis())) is MessageDoubt.NONE


def test_normalized_match_restores_html_entity_source_span():
    text = "&#xC571;·다 운.로-드 부탁드립니다"

    candidates = message_candidates(text, analysis())

    assert candidates == [
        MessageCandidate(
            doubt=MessageDoubt.APP_INSTALL,
            evidence="&#xC571;·다 운.로-드",
            start=0,
        )
    ]


def test_select_doubt_removes_only_overlapping_generic_candidates():
    candidates = [
        MessageCandidate(MessageDoubt.IDENTITY_CHECK, "본인 확인", 0),
        MessageCandidate(MessageDoubt.DATA_INPUT, "본인 확인 후 번호 입력", 0),
        MessageCandidate(MessageDoubt.OPEN_LINK, "클릭", 20),
    ]

    assert select_message_doubt(candidates) is MessageDoubt.DATA_INPUT


def test_select_doubt_preserves_earlier_independent_generic_request():
    candidates = [
        MessageCandidate(MessageDoubt.OPEN_LINK, "첫 링크 클릭", 0),
        MessageCandidate(MessageDoubt.CANCEL_REFUND, "환불 진행", 20),
    ]

    assert select_message_doubt(candidates) is MessageDoubt.OPEN_LINK


def test_select_doubt_prefers_overlapping_detail_request_to_link_click():
    candidates = [
        MessageCandidate(MessageDoubt.OPEN_LINK, "링크를 클릭", 0),
        MessageCandidate(MessageDoubt.DETAIL_VIEW, "클릭하여 공지 확인", 4),
    ]

    assert select_message_doubt(candidates) is MessageDoubt.DETAIL_VIEW
