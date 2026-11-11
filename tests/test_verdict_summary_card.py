"""D19: 판정 카드(R6b, R9, V1)는 그림을 넣은 요약 카드와 '자세히 보기'로 나눈다.

카드 앞면에는 할 일, 근거 한 줄, 확인하지 못한 것 한 줄만 두고(basicCard 본문 230자),
앞면에서 뺀 주장과 확인한 것, 확인하지 못한 것은 '자세히 보기' 캐러셀(장당 128자)에 둔다.
규칙: docs/designs/chatbot-copy.md 3절 "판정 요약 카드", 4절 "판정 요약 카드에서 옮긴 내용".
"""

import pytest

import main
from ai.case.types import (
    ClaimView,
    Conclusion,
    MessageAnswers,
    MessageCaseResult,
    NumberCheck,
    OfficialContact,
    Scenario,
    StopAction,
    VoiceCaseResult,
)
from services.result_card_renderer import (
    render_detail_carousel,
    render_link_caution_card,
    render_message_case_card,
    render_message_detail_carousel,
    render_result_card,
    render_voice_detail_carousel,
    render_voice_result_card,
)

IMAGE_BASE = (
    "https://raw.githubusercontent.com/kakaotechcampus-4/ktc4-chonnam-1/develop/"
    "frontend/kakao-templates/assets/cards/"
)
DANGER_IMAGE = IMAGE_BASE + "r1-danger.png"
UNCERTAIN_IMAGE = IMAGE_BASE + "r3-inconclusive.png"
DETAIL_BLOCK = "6aad3abfe745d3358bd90c8d"


def _card(response: dict, index: int = 0) -> dict:
    return response["template"]["outputs"][index]["basicCard"]


def _items(response: dict) -> list[dict]:
    return response["template"]["outputs"][0]["carousel"]["items"]


def _family(
    *, conclusion=Conclusion.INCONCLUSIVE, caution=True, answer=None, basis=False,
    stop=(StopAction.DONT_SEND_MONEY,), demands=None, quote="엄마 나야",
) -> dict:
    checked = ["message_text"] + (["user_answer"] if answer else [])
    return MessageCaseResult(
        scenario=Scenario.IMPERSONATION_NO_LINK,
        conclusion=conclusion,
        caution=caution,
        stop_actions=list(stop),
        claims=ClaimView(
            identity=[{"kind": "acquaintance", "name": "엄마", "quote": quote, "listed_institution": False}],
            blockers=["폰 고장나서 전화가 안돼"],
            demands=demands if demands is not None else [{"kind": "money_transfer", "quote": "돈 좀 보내줘"}],
        ),
        verify_actions=["check_account_holder"] if answer == "is_person" else [],
        checked=checked,
        unchecked=[
            {"item": "similar_cases", "reason": "kb_empty"},
            {"item": "actual_sender", "reason": "cannot_verify"},
        ],
        basis_from_answer=basis,
        answers=MessageAnswers(called_known_number=answer),
        explanation="x",
    ).model_dump(mode="json")


def _institution(caution=True) -> dict:
    return MessageCaseResult(
        scenario=Scenario.IMPERSONATION_NO_LINK,
        conclusion=Conclusion.INCONCLUSIVE,
        caution=caution,
        stop_actions=[StopAction.DONT_CALL_NUMBER_IN_MESSAGE],
        claims=ClaimView(
            identity=[{"kind": "institution", "name": "청소행정과", "quote": "청소행정과입니다", "listed_institution": False}],
            pretexts=["쓰레기 무단투기 신고가 접수되었습니다"],
        ),
        checked=["message_text"],
        unchecked=[{"item": "actual_sender", "reason": "cannot_verify"}],
        explanation="x",
    ).model_dump(mode="json")


def _voice(*, conclusion=Conclusion.SUSPECTED, caution=True, number_checks=None) -> dict:
    return VoiceCaseResult(
        scenario=Scenario.VOICE_PHISHING,
        conclusion=conclusion,
        caution=caution,
        stop_actions=[StopAction.HANG_UP, StopAction.DONT_SEND_MONEY],
        claims=ClaimView(
            identity=[{"kind": "institution", "name": "금융감독원", "quote": "금융감독원 조사국입니다", "listed_institution": True}],
            demands=[{"kind": "money_transfer", "quote": "안전계좌로 옮기셔야 합니다"}],
        ),
        checked=["transcript"],
        unchecked=[{"item": "actual_sender", "reason": "cannot_verify"}],
        number_checks=number_checks or [],
        official_contact=OfficialContact(org="금융감독원", number="1332", source_url="https://fss.or.kr"),
        explanation="x",
    ).model_dump(mode="json")


def _part(answer: str, brand: str | None = None, reason: str = "x") -> dict:
    return {
        "brand": brand, "category": None, "answer": answer,
        "details": {"doubts": [], "signals": [], "reason": {"text": reason, "failures": []}},
    }


def _link(official: str, brand: str | None = None, reason: str = "x") -> dict:
    return {
        "url": {"final_url": None, "domain": "evil.top", "official": official,
                "scan": {"score": None, "scanned_at": None}},
        "message": _part("no_risk_found", brand=brand, reason=reason),
        "env": _part("not_run"),
        "result": False,
    }


# ── R6b 요약 카드 ─────────────────────────────────────────────


class TestMessageSummaryCard:
    def test_caution_family_card_is_short_basic_card_with_image_and_detail(self):
        response = render_message_case_card(_family(), "job-1")
        card = _card(response)

        assert card["title"] == "🔎 주의가 필요한 문자예요"
        assert card["description"] == (
            "돈을 보내지 마세요.\n"
            "원래 알던 번호로 직접 전화해 확인하세요.\n\n"
            "⚠️ 내세운 관계: '엄마 나야'\n"
            "❌ 실제로 보낸 사람은 확인하지 못했어요."
        )
        # 주의 이미지는 아직 없어 판단 보류 이미지를 쓴다
        assert card["thumbnail"]["imageUrl"] == UNCERTAIN_IMAGE
        assert [b["label"] for b in card["buttons"]] == ["자세히 보기", "다른 문자 확인하기"]
        assert card["buttons"][0]["blockId"] == DETAIL_BLOCK
        assert card["buttons"][0]["extra"] == {"job_id": "job-1"}

    def test_answer_that_changed_the_conclusion_is_the_basis_line(self):
        result = _family(conclusion=Conclusion.SUSPECTED, answer="not_person", basis=True)
        card = _card(render_message_case_card(result, "job-1"))

        assert card["title"] == "⚠️ 사칭 문자로 의심돼요"
        assert card["thumbnail"]["imageUrl"] == DANGER_IMAGE
        lines = card["description"].splitlines()
        assert "✅ 원래 번호로 통화해 보니 본인이 아니라고 하셨어요." in lines
        # 같은 일을 하는 basis_note 줄은 앞면에 쓰지 않는다
        assert "답해 주신 내용 기준으로 안내했어요." not in card["description"]
        assert "주의할 점" not in card["description"]

    def test_is_person_answer_keeps_uncertain_image(self):
        result = _family(caution=False, answer="is_person", basis=True)
        card = _card(render_message_case_card(result, "job-1"))

        assert card["title"] == "🤔 판단하기 어려워요"
        assert card["thumbnail"]["imageUrl"] == UNCERTAIN_IMAGE
        assert card["description"].splitlines()[1] == "보내기 전에 받는 계좌가 본인 명의인지 한 번 더 확인하세요."
        assert "✅ 원래 번호로 통화해 보니 본인이 맞다고 하셨어요." in card["description"]

    def test_institution_message_uses_institution_quote_and_sender_place(self):
        card = _card(render_message_case_card(_institution(), "job-1"))

        assert "⚠️ 내세운 기관: '청소행정과입니다'" in card["description"]
        assert card["description"].endswith("❌ 실제로 보낸 곳은 확인하지 못했어요.")

    def test_uncertain_branch_marks_quote_as_message_content(self):
        card = _card(render_message_case_card(_family(caution=False), "job-1"))

        assert "📩 내세운 관계: '엄마 나야'" in card["description"]

    def test_long_values_still_fit_basic_card(self):
        result = _family(
            stop=(StopAction.DONT_SEND_MONEY, StopAction.DONT_SHARE_AUTH_CODE, StopAction.DONT_INSTALL_APP),
            quote="엄마야 폰 액정이 깨져서 수리 맡겼어 이 번호로 연락해 급해",
        )
        card = _card(render_message_case_card(result, "job-1"))

        assert len(card["description"]) <= 230
        assert "…'" in card["description"]  # 인용은 30자에서 줄인다

    def test_questions_still_follow_the_summary_card(self):
        result = _family()
        result["questions"] = [
            {"question_id": "already_acted", "option_ids": ["not_yet"]},
            {"question_id": "called_known_number", "option_ids": ["not_person"]},
        ]
        outputs = render_message_case_card(result, "job-1")["template"]["outputs"]

        assert [list(o) for o in outputs] == [["basicCard"], ["listCard"], ["listCard"]]

    def test_no_job_id_keeps_detail_button_without_extra(self):
        card = _card(render_message_case_card(_family()))

        assert [b["label"] for b in card["buttons"]] == ["자세히 보기", "다른 문자 확인하기"]
        assert "extra" not in card["buttons"][0]


# ── R6b 자세히 보기 ───────────────────────────────────────────


class TestMessageDetailCarousel:
    def test_claims_checked_and_scope_cards(self):
        items = _items(render_message_detail_carousel(_family(answer="not_person", basis=True)))

        assert [i["title"] for i in items] == ["⚠️ 주의할 점", "✅ 확인한 것", "📋 확인 범위"]
        assert items[0]["description"] == (
            "- 내세운 관계: '엄마 나야'\n- 내세운 사정: '폰 고장나서 전화가 안돼'\n- 요구한 것: 송금·입금"
        )
        assert items[1]["description"] == (
            "- 문자 내용\n- 원래 번호로 통화한 결과 (본인이 아니라고 답해 주셨어요)"
        )
        assert items[2]["description"].startswith(
            "- 비슷한 사례 (비교할 사례를 찾지 못했어요)\n- 실제로 보낸 사람\n\n"
        )
        assert [b["label"] for b in items[-1]["buttons"]] == ["다른 문자 확인하기"]
        assert all("buttons" not in i or not i["buttons"] for i in items[:-1])

    def test_every_card_fits_carousel_limit(self):
        result = _family(
            quote="엄마야 폰 액정이 깨져서 수리 맡겼어 이 번호로 연락해 급해",
            demands=[
                {"kind": "money_transfer", "quote": "a"}, {"kind": "gift_card", "quote": "b"},
                {"kind": "auth_code", "quote": "c"},
            ],
        )
        result["claims"]["pretexts"] = ["보험금 청구 서류를 급하게 내야 해서 그래 미안해 엄마가 정말"]
        for item in _items(render_message_detail_carousel(result)):
            assert len(item["description"]) <= 128, item

    def test_without_claims_the_claims_card_is_dropped(self):
        result = _family()
        result["claims"] = {"identity": [], "pretexts": [], "demands": [], "blockers": []}

        titles = [i["title"] for i in _items(render_message_detail_carousel(result))]
        assert titles == ["✅ 확인한 것", "📋 확인 범위"]

    def test_detail_button_reaches_message_carousel(self):
        main.ANALYSIS_JOBS["d19-msg"] = {
            "analysis_kind": "message_case", "final_payload": _family(), "status": "completed",
        }
        try:
            response = main.handle_detail_carousel("d19-msg")
        finally:
            main.ANALYSIS_JOBS.pop("d19-msg", None)

        assert response == render_message_detail_carousel(_family())


# ── V1 요약 카드와 자세히 보기 ────────────────────────────────


class TestVoiceSummaryCard:
    def test_number_check_is_the_strongest_basis(self):
        result = _voice(number_checks=[
            NumberCheck(number="02-1234-5678", org="금융감독원", result="not_official").model_dump(mode="json"),
        ])
        card = _card(render_voice_result_card(result, "job-v"))

        assert card["title"] == "⚠️ 보이스피싱이 의심돼요"
        assert card["thumbnail"]["imageUrl"] == DANGER_IMAGE
        assert card["description"] == (
            "통화를 끊고, 돈을 보내지 마세요.\n"
            "금융감독원 대표번호(1332)로\n직접 전화해 확인하세요.\n\n"
            "✅ 상대가 말한 번호(02-1234-5678): 금융감독원 대표번호가 아니에요\n"
            "❌ 실제로 건 곳은 확인하지 못했어요."
        )
        assert card["buttons"][0]["extra"] == {"job_id": "job-v"}

    def test_without_number_check_uses_institution_quote(self):
        card = _card(render_voice_result_card(_voice(), "job-v"))

        assert "⚠️ 통화에 나온 기관: '금융감독원 조사국입니다'" in card["description"]

    @pytest.mark.parametrize("caution, title, mark", [
        (True, "🔎 주의가 필요한 통화예요", "⚠️"),
        (False, "🤔 판단하기 어려워요", "📞"),
    ])
    def test_inconclusive_branches_use_uncertain_image(self, caution, title, mark):
        card = _card(render_voice_result_card(
            _voice(conclusion=Conclusion.INCONCLUSIVE, caution=caution), "job-v",
        ))

        assert card["title"] == title
        assert card["thumbnail"]["imageUrl"] == UNCERTAIN_IMAGE
        assert f"{mark} 통화에 나온 기관:" in card["description"]
        assert len(card["description"]) <= 230


class TestVoiceDetailCarousel:
    def test_checked_card_sits_before_scope_card(self):
        result = _voice(number_checks=[
            NumberCheck(number="02-1234-5678", org="금융감독원", result="not_official").model_dump(mode="json"),
        ])
        titles = [i["title"] for i in _items(render_voice_detail_carousel(result))]

        assert titles.index("✅ 확인한 것") == titles.index("📋 확인 범위") - 1
        checked = _items(render_voice_detail_carousel(result))[titles.index("✅ 확인한 것")]
        assert "상대가 말한 번호(02-1234-5678): 금융감독원 대표번호가 아니에요" in checked["description"]
        assert len(checked["description"]) <= 128


# ── R9 요약 카드와 자세히 보기 ────────────────────────────────


class TestR9SummaryCard:
    def test_r9_is_basic_card_with_uncertain_image(self):
        card = _card(render_result_card(_link("unresolved"), job_id="job-r9"))

        assert card["title"] == "🤔 판단하기 어려워요"
        assert card["thumbnail"]["imageUrl"] == UNCERTAIN_IMAGE
        assert card["description"] == (
            "문자 속 링크 대신 보낸 기관의\n"
            "공식 앱이나 홈페이지에서 직접 확인하세요.\n\n"
            "❌ 주소가 공식 주소인지 확인하지 못했어요.\n"
            "⏰ 기한이 있다면 미리 공식 경로로 확인하세요."
        )
        assert card["buttons"][0]["extra"] == {"job_id": "job-r9"}

    def test_checked_address_line_is_the_basis(self):
        # 공식 주소여도 문자, 페이지가 모두 위험 신호 없음으로 끝나지 않으면 R9다
        card = _card(render_result_card(_link("official", brand="CJ대한통운")))
        assert "✅ 주소가 CJ대한통운 공식 주소와 같아요" in card["description"]

    def test_r9_detail_has_checked_card_and_message_reason(self):
        analysis = _link("not_registered", brand="CJ대한통운", reason="택배 배송을 내세운 문자입니다.")
        items = _items(render_detail_carousel(analysis))
        titles = [i["title"] for i in items]

        # 앞면에서 뺀 확인한 것, 확인하지 못한 것(이유 포함)은 확인 범위(C2) 앞에 장으로 둔다
        assert titles.index("✅ 확인한 것") + 1 == titles.index("❌ 확인하지 못한 것")
        assert titles.index("❌ 확인하지 못한 것") + 1 == titles.index("📋 확인 범위")
        checked = items[titles.index("✅ 확인한 것")]["description"]
        assert "- 문자에 적힌 보낸 곳: CJ대한통운" in checked
        unchecked = items[titles.index("❌ 확인하지 못한 것")]["description"]
        assert "- 실제로 보낸 곳" in unchecked
        assert "(공식 주소 목록에 없는 기관이라 대조하지 못했어요)" in unchecked
        # #68: 택배사가 아닌 기관의 명분은 이 칸으로만 전달된다. 앞면에서 빠진 만큼 상세에 둔다
        assert any(i["title"] == "📩 문자에 적힌 내용" and "택배 배송을 내세운" in i["description"] for i in items)
        for item in items:
            assert len(item["description"]) <= 128, item


# ── R9 자리의 주의 갈래(#89) ──────────────────────────────────


class TestLinkCautionSummaryCard:
    def test_link_caution_is_summary_card_with_detail(self):
        response = render_link_caution_card(_link("not_registered"), _institution(), job_id="job-l")
        card = _card(response)

        assert card["title"] == "🔎 주의가 필요한 문자예요"
        assert card["thumbnail"]["imageUrl"] == UNCERTAIN_IMAGE
        assert "⚠️ 내세운 기관: '청소행정과입니다'" in card["description"]
        assert card["buttons"][0]["extra"] == {"job_id": "job-l"}

    def test_link_caution_detail_uses_case_lines(self):
        main.ANALYSIS_JOBS["d19-link"] = {
            "analysis_kind": "link", "link_count": 1, "status": "completed",
            "final_payload": _link("not_registered"), "link_case": _institution(),
        }
        try:
            items = _items(main.handle_detail_carousel("d19-link"))
        finally:
            main.ANALYSIS_JOBS.pop("d19-link", None)

        titles = [i["title"] for i in items]
        assert titles[:3] == ["⚠️ 주의할 점", "✅ 확인한 것", "❌ 확인하지 못한 것"]
        unchecked = items[titles.index("❌ 확인하지 못한 것")]["description"]
        assert "- 실제로 보낸 곳" in unchecked
