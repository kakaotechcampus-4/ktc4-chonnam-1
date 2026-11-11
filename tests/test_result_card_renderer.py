"""services.result_card_renderer 테스트.

select_result_card()가 고른 카드를 실제로 값까지 채워서 돌려주는지,
job_id가 "자세히 보기" 버튼에 꽂히는지, 캐러셀이 10장 상한을 지키는지를
검증한다. 카드 선택 로직 자체(언제 어떤 카드를 고르는가)는
test_result_card_selector.py의 몫이라 여기서는 다시 다루지 않는다.
"""

from types import SimpleNamespace

from services import result_card_renderer
from services.result_card_renderer import (
    MAX_CAROUSEL_SIGNAL_ITEMS,
    MAX_CAROUSEL_TOTAL_ITEMS,
    merge_kakao_responses,
    render_detail_carousel,
    render_result_card,
    _r9_blank_values,
)

from services.chatbot_copy_data import (
    get_message_failure_copy,
    MESSAGE_FAILURE_COPY,
)


def _guidance(kind="verification", text="본인 명의 계좌인지 우체국 공식 앱에서 확인하세요.", source="인터넷우체국 공지", published_at="2024-03-06", rule_id="NR-0015"):
    return SimpleNamespace(kind=kind, text=text, source=source, published_at=published_at, rule_id=rule_id)

def _part(
    answer: str, brand: str | None = None, signals: list | None = None,
    doubts: list | None = None, failures: list | None = None,
) -> dict:
    return {
        "brand": brand,
        "category": None,
        "answer": answer,
        "details": {
            "doubts": doubts or [],
            "signals": signals or [],
            "reason": {"text": "x", "failures": failures or []},
        },
    }


def _doubt(value: str, evidence: str = "e") -> dict:
    return {"value": value, "evidence": evidence}


def _signal(code: str = "credential_request", evidence: str = "비밀번호를 입력해주세요") -> dict:
    return {"code": code, "evidence": evidence}


def _analysis(official: str, message: dict, env: dict, domain: str = "example.com") -> dict:
    return {
        "url": {
            "final_url": f"https://{domain}",
            "domain": domain,
            "official": official,
            "scan": {"score": None, "scanned_at": None},
        },
        "message": message,
        "env": env,
        "result": False,
    }


class TestRenderResultCard:
    def test_official_card_fills_org_name_and_official_url(self):
        analysis = _analysis(
            "official",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("no_risk_found"),
            domain="cjlogistics.com",
        )

        card = render_result_card(analysis)

        text_card = card["template"]["outputs"][0]["basicCard"]
        assert "CJ대한통운" in text_card["title"]
        buttons = {b["label"]: b for b in text_card["buttons"]}
        assert buttons["공식 홈페이지 열기"]["webLinkUrl"] == "https://cjlogistics.com"

    def test_official_card_without_message_brand_uses_domain_brand(self):
        # #164: 문자에 기관 이름이 없으면 주소로 찾은 공식 기관 이름과 홈페이지를 쓴다
        analysis = _analysis(
            "official",
            _part("no_risk_found", brand="unknown"),
            _part("no_risk_found"),
            domain="www.hanjin.com",
        )

        card = render_result_card(analysis)

        text_card = card["template"]["outputs"][0]["basicCard"]
        assert "한진택배" in text_card["title"]
        buttons = {b["label"]: b for b in text_card["buttons"]}
        assert buttons["공식 홈페이지 열기"]["webLinkUrl"] == "https://hanjin.com"

    def test_not_official_card_fills_org_name(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("no_risk_found", brand="누리몰"),
            _part("not_run"),
        )

        card = render_result_card(analysis)

        basic_card = card["template"]["outputs"][0]["basicCard"]
        assert "누리몰" in basic_card["description"]

    def test_no_job_id_means_no_extra_injected(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        card = render_result_card(analysis)

        text_card = card["template"]["outputs"][0]["basicCard"]
        detail_button = next(
            (b for b in text_card["buttons"] if b["label"] == "자세히 보기"), None
        )
        if detail_button is not None:
            assert "extra" not in detail_button

    def test_card_without_detail_button_ignores_job_id(self):
        """r2-official처럼 "자세히 보기" 버튼이 없는 카드는 job_id를 줘도
        그냥 무시한다 (예외가 나면 안 된다)."""
        analysis = _analysis(
            "official",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("no_risk_found"),
        )

        card = render_result_card(analysis, job_id="job-123")

        assert card["version"] == "2.0"

    def test_disabling_details_preserves_all_official_card_buttons(self):
        analysis = _analysis(
            "official", _part("no_risk_found", brand="CJ대한통운"),
            _part("no_risk_found"), domain="cjlogistics.com",
        )

        assert render_result_card(analysis, job_id="job-123", allow_details=False) == render_result_card(
            analysis, job_id="job-123",
        )


_R9_MOVED_TITLES = ("📩 문자에 적힌 내용", "✅ 확인한 것", "❌ 확인하지 못한 것")
# D19: 판단 보류(R9) 결과의 캐러셀에는 위 세 장이 C2 앞에 붙는다
_R9_DETAIL_ITEMS = len(_R9_MOVED_TITLES)


def _r9_description(analysis: dict) -> str:
    """D19: R9 앞면은 요약 카드이고, 확인한 것, 확인하지 못한 것, 문자에 적힌 내용은
    '자세히 보기'의 장으로 옮겼다. 빈칸을 채우는 규칙은 그대로라 두 곳을 이어 붙여 본다."""

    front = render_result_card(analysis)["template"]["outputs"][0]["basicCard"]["description"]
    items = render_detail_carousel(analysis)["template"]["outputs"][0]["carousel"]["items"]
    moved = [f"{i['title']}\n{i['description']}" for i in items if i["title"] in _R9_MOVED_TITLES]
    return "\n\n".join([front, *moved])


def _c2_text(analysis: dict) -> str:
    carousel = render_detail_carousel(analysis)
    items = carousel["template"]["outputs"][0]["carousel"]["items"]
    return str(items[-2])  # C2는 끝에서 두 번째(마지막은 C3 행동 안내)


class TestR9BlankValues:
    """docs/designs/chatbot-copy.md "R9 판단 보류 → 빈칸 채우는 규칙" 표를
    그대로 검증한다. 멘토 리뷰(PR #39) 반영으로 추가된 빈칸 연동."""

    def test_org_label_uses_brand_when_known(self):
        analysis = _analysis(
            "not_registered", _part("no_risk_found", brand="CJ대한통운"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 속 링크 대신 CJ대한통운\n공식 앱이나 홈페이지" in description

    def test_org_label_falls_back_when_brand_unknown(self):
        analysis = _analysis(
            "unresolved", _part("no_risk_found", brand="unknown"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 속 링크 대신 보낸 기관의\n공식 앱이나 홈페이지" in description

    def test_sender_always_goes_to_unchecked_even_when_brand_known(self):
        analysis = _analysis(
            "not_registered", _part("no_risk_found", brand="CJ대한통운"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 문자에 적힌 보낸 곳: CJ대한통운" in description
        assert "- 실제로 보낸 곳" in description

    def test_sender_unknown_has_no_checked_line_but_still_unchecked(self):
        analysis = _analysis(
            "unresolved", _part("no_risk_found", brand=None), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자에 적힌 보낸 곳" not in description
        assert "- 실제로 보낸 곳" in description

    def test_requested_actions_exclude_unknown_and_cap_at_three_in_order(self):
        analysis = _analysis(
            "not_registered",
            _part(
                "no_risk_found", brand="CJ대한통운",
                doubts=[
                    _doubt("앱 설치"), _doubt("unknown"), _doubt("정보 입력"),
                    _doubt("수령·일정 확인"), _doubt("금전 인출"),
                ],
            ),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 문자에서 요구한 것: 앱 설치, 정보 입력, 수령·일정 확인" in description
        assert "금전 인출" not in description

    def test_no_requested_actions_omits_the_line(self):
        analysis = _analysis(
            "not_registered", _part("no_risk_found", brand="CJ대한통운"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자에서 요구한 것" not in description

    def test_address_official_with_known_brand(self):
        analysis = _analysis(
            "official", _part("no_risk_found", brand="CJ대한통운"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 주소가 CJ대한통운 공식 주소와 같아요" in description

    def test_address_official_without_brand(self):
        analysis = _analysis(
            "official", _part("no_risk_found", brand=None), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 주소가 공식 주소 목록에 있어요" in description

    def test_address_not_registered_and_brand_mismatch_have_distinct_wording(self):
        not_registered = _analysis(
            "not_registered",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("not_run"),
        )
        brand_mismatch = _analysis(
            "brand_mismatch",
            _part("no_risk_found", brand="unknown"),
            _part("not_run"),
        )

        not_registered_description = _r9_description(not_registered)
        brand_mismatch_description = _r9_description(brand_mismatch)

        assert (
            "- 주소가 공식 주소인지 "
            "(공식 주소 목록에 없는 기관이라 대조하지 못했어요)"
            in not_registered_description
        )
        assert (
            "- 공식 주소 목록에 없는 주소예요"
            in brand_mismatch_description
        )

    def test_address_unresolved_goes_to_unchecked(self):
        analysis = _analysis(
            "unresolved", _part("no_risk_found", brand=None), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 주소가 공식 주소인지" in description

    def test_not_registered_without_brand_does_not_claim_checked_the_list(self):
        """멘토 리뷰(PR #54): brand가 없으면 check_official_domain()은 도메인을
        보지도 않고 not_registered를 반환한다 — 실제로는 화이트리스트에 있는
        naver.com이어도 마찬가지다. "목록에 없는 주소"라고 단정하면 안 된다."""

        analysis = _analysis(
            "not_registered", _part("no_risk_found", brand=None), _part("not_run"),
            domain="naver.com",
        )

        description = _r9_description(analysis)

        assert "공식 주소 목록에 없는 주소예요" not in description
        assert (
            "- 주소가 공식 주소인지 (보낸 기관을 몰라 대조하지 못했어요)"
            in description
        )

    def test_message_partial_is_unchecked(self):
        analysis = _analysis(
            "not_registered",
            _part("partial", brand="CJ대한통운"),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 일부에서 위험 신호를 찾지 못했어요" not in description
        assert "- 문자 내용 (문자 분석을 완료하지 못했어요)" in description

    def test_message_partial_is_only_unchecked(self):
        analysis = _analysis(
            "not_registered",
            _part("partial", brand="CJ대한통운"),
            _part("not_run"),
        )

        values = _r9_blank_values(analysis)

        assert "- 문자 내용 (문자 분석을 완료하지 못했어요)" in values["unchecked"]
        assert "문자 내용 (문자 분석을 완료하지 못했어요)" not in values["checked"]

    def test_message_partial_from_timeout_is_treated_as_unchecked(self):
        """멘토 리뷰(PR #54)·이슈 #59: 시간 초과는 어디까지 분석했는지
        경계가 불확실해 "일부에서 위험 신호를 찾지 못했다"고 단정하면
        안 되고, 실제 실패 원인(timeout)을 구체적으로 안내한다."""

        analysis = _analysis(
            "not_registered",
            _part("partial", brand="CJ대한통운", failures=["timeout"]),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 일부에서 위험 신호를 찾지 못했어요" not in description
        assert "문자 나머지" not in description
        assert "- 문자 내용 (분석 시간이 초과되어 끝까지 확인하지 못했어요)" in description

    def test_message_failed_and_not_run_have_different_wording(self):
        failed = _analysis(
            "not_registered",
            _part("failed", brand="CJ대한통운"),
            _part("not_run"),
        )
        not_run = _analysis(
            "not_registered",
            _part("not_run", brand="CJ대한통운"),
            _part("not_run"),
        )

        assert (
            "- 문자 내용 (문자 분석을 완료하지 못했어요)"
            in _r9_description(failed)
        )
        assert "- 문자 내용" in _r9_description(not_run)
        assert (
            "문자 내용 (문자 분석을 완료하지 못했어요)"
            not in _r9_description(not_run)
        )

    def test_message_failure_reason_is_shown_for_each_known_code(self):
        """이슈 #59: timeout/refused/invalid_output/llm_error 각각 다른
        설명이 나와야 한다."""

        expected = {
            "timeout": "분석 시간이 초과되어 끝까지 확인하지 못했어요",
            "refused": "분석 요청이 거절되어 확인하지 못했어요",
            "invalid_output": "분석 결과를 올바르게 받지 못했어요",
            "llm_error": "분석 중 오류가 발생해 확인하지 못했어요",
        }

        for code, phrase in expected.items():
            analysis = _analysis(
                "not_registered",
                _part("partial", brand="CJ대한통운", failures=[code]),
                _part("not_run"),
            )

            assert f"- 문자 내용 ({phrase})" in _r9_description(analysis)

    def test_message_failure_uses_only_the_first_reason(self):
        """이슈 #59: 복수 실패 원인이 있으면 첫 번째 원인만 표시한다."""

        analysis = _analysis(
            "not_registered",
            _part(
                "partial",
                brand="CJ대한통운",
                failures=["timeout", "llm_error"],
            ),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 문자 내용 (분석 시간이 초과되어 끝까지 확인하지 못했어요)" in description
        assert "분석 중 오류가 발생해 확인하지 못했어요" not in description

    def test_message_failure_preserves_first_reason_order(self):
        first = get_message_failure_copy(["timeout", "llm_error"])
        second = get_message_failure_copy(["llm_error", "timeout"])

        assert first == MESSAGE_FAILURE_COPY["timeout"]
        assert second == MESSAGE_FAILURE_COPY["llm_error"]

    def test_unknown_message_failure_code_falls_back_safely(self):
        """이슈 #59: 원인 정보가 부족하거나 모르는 코드여도 렌더링이
        깨지지 않는다."""

        analysis = _analysis(
            "not_registered",
            _part("partial", brand="CJ대한통운", failures=["some_future_code"]),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 문자 내용 (문자 분석을 완료하지 못했어요)" in description

    def test_link_only_message_shows_empty_input_reason(self):
        """이슈 #78 (#59 후속): 링크만 보내 본문이 비면 분석 실패가 아니라
        '읽을 글이 없었다'는 뜻이다 — 기본 문구로 뭉뚱그리면 안 된다."""

        analysis = _analysis(
            "not_registered",
            _part("failed", brand="CJ대한통운", failures=["empty_input"]),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 문자 내용 (링크만 있어서 확인할 글이 없었어요)" in description

    def test_too_long_message_shows_input_too_large_reason(self):
        """이슈 #78: 본문이 8,192자를 넘어 검사를 못 한 경우도 검사
        실패와 구분한다."""

        analysis = _analysis(
            "not_registered",
            _part("failed", brand="CJ대한통운", failures=["input_too_large"]),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "- 문자 내용 (문자가 너무 길어 끝까지 확인하지 못했어요)" in description

    def test_env_partial_from_timeout_is_treated_as_unchecked(self):
        analysis = _analysis(
            "not_registered",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("partial", failures=["timeout"]),
        )

        description = _r9_description(analysis)

        assert "페이지 일부에서 위험 신호를 찾지 못했어요" not in description
        assert "- 페이지 내용 (끝까지 확인하지 못했어요)" in description

    def test_env_partial_without_timeout_is_unchecked(self):
        analysis = _analysis(
            "not_registered",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("partial"),
        )

        description = _r9_description(analysis)

        assert "페이지 일부에서 위험 신호를 찾지 못했어요" not in description
        assert "- 페이지 내용 (끝까지 확인하지 못했어요)" in description

    def test_env_failed_and_not_run_have_different_wording(self):
        failed = _analysis(
            "not_registered", _part("no_risk_found", brand="CJ대한통운"), _part("failed"),
        )
        not_run = _analysis(
            "not_registered", _part("no_risk_found", brand="CJ대한통운"), _part("not_run"),
        )

        assert "- 페이지 내용 (열지 못했어요)" in _r9_description(failed)
        assert "- 페이지 내용" in _r9_description(not_run)
        assert "열지 못했어요" not in _r9_description(not_run)

    def test_no_checked_facts_at_all_falls_back_to_none(self):
        analysis = _analysis(
            "unresolved", _part("failed", brand=None), _part("failed"),
        )

        description = _r9_description(analysis)

        assert "확인한 것\n- 없음" in description


def _part_with_reason(
    answer: str, reason_text: str | None, brand: str | None = None,
) -> dict:
    return {
        "brand": brand,
        "category": None,
        "answer": answer,
        "details": {
            "doubts": [],
            "signals": [],
            "reason": {"text": reason_text, "failures": []},
        },
    }


class TestR9MessageReason:
    """이슈 #68: `문자에 적힌 내용`(message_reason)이 message.brand가
    모르는 기관(택배사 아닌 발신처)의 명분·요구까지 전달하는지 검증한다."""

    def test_reason_text_appears_under_the_new_section(self):
        analysis = _analysis(
            "unresolved",
            _part_with_reason(
                "no_risk_found",
                "문자가 내세운 기관: '청소행정과'. 문자가 내세운 명분: '쓰레기 무단투기 신고가 접수되었습니다.'.",
            ),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자에 적힌 내용\n문자가 내세운 기관: '청소행정과'." in description

    def test_missing_reason_text_falls_back_to_default_copy(self):
        analysis = _analysis(
            "unresolved",
            _part_with_reason("no_risk_found", None),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자에 적힌 내용\n문자 분석 결과를 전달받지 못했습니다." in description

    def test_blank_reason_text_falls_back_to_default_copy(self):
        analysis = _analysis(
            "unresolved",
            _part_with_reason("no_risk_found", "   "),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자에 적힌 내용\n문자 분석 결과를 전달받지 못했습니다." in description

    def test_summary_and_reason_card_stay_within_limits_and_truncate_reason(self):
        long_reason = "문자가 내세운 명분: '" + "가" * 500 + "'."
        analysis = _analysis(
            "not_registered",
            _part_with_reason(
                "partial", long_reason, brand="CJ대한통운",
            ),
            _part("partial"),
        )
        analysis["message"]["details"]["doubts"] = [
            _doubt("상세 내용 확인"), _doubt("개인정보 입력"), _doubt("입금"),
        ]
        analysis["message"]["details"]["reason"]["failures"] = ["timeout"]
        analysis["env"]["details"]["reason"]["failures"] = []

        # D19: 앞면은 basicCard 230자, 문자에 적힌 내용은 '자세히 보기'의 한 장(128자)이다
        front = render_result_card(analysis)["template"]["outputs"][0]["basicCard"]["description"]
        items = render_detail_carousel(analysis)["template"]["outputs"][0]["carousel"]["items"]
        reason = next(i for i in items if i["title"] == "📩 문자에 적힌 내용")["description"]

        assert len(front) <= 230
        assert front.endswith("기한이 있다면 미리 공식 경로로 확인하세요.")
        assert len(reason) <= 128
        # 잘렸으면 원문 전체가 다 들어갈 리 없다.
        assert reason.endswith("…")
        assert long_reason not in reason

    def test_other_blank_values_are_not_affected_by_message_reason(self):
        """message_reason 치환이 이미 채워진 {{ checked }}/{{ unchecked }}
        등 다른 빈칸을 건드리면 안 된다."""
        analysis = _analysis(
            "not_registered",
            _part_with_reason("no_risk_found", "문자가 내세운 명분: 테스트", brand="CJ대한통운"),
            _part("no_risk_found"),
        )

        description = _r9_description(analysis)

        assert "문자 속 링크 대신 CJ대한통운\n공식 앱이나" in description
        assert "- 문자에서 위험 신호를 찾지 못했어요" in description
        assert "{{ message_reason }}" not in description


class TestMergeKakaoResponses:
    def test_empty_list_falls_back_to_unavailable_card(self):
        merged = merge_kakao_responses([])

        assert merged["version"] == "2.0"
        assert "template" in merged

    def test_single_response_is_returned_as_is(self):
        response = {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "a"}}]}}

        assert merge_kakao_responses([response]) is response

    def test_multiple_responses_concatenate_outputs(self):
        first = {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "a"}}]}}
        second = {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": "b"}}]}}

        merged = merge_kakao_responses([first, second])

        assert merged["template"]["outputs"] == [
            {"simpleText": {"text": "a"}},
            {"simpleText": {"text": "b"}},
        ]


class TestRenderDetailCarousel:
    def test_one_signal_produces_c1_plus_c2_plus_c3(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        carousel = render_detail_carousel(analysis)

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == 1 + 1 + 1

    def test_signal_copy_is_filled_from_known_code(self):
        analysis = _analysis(
            "brand_mismatch",
            _part(
                "risk_found",
                brand="CJ대한통운",
                signals=[_signal("credential_request", "비밀번호를 입력해주세요")],
            ),
            _part("not_run"),
        )

        carousel = render_detail_carousel(analysis)

        c1 = carousel["template"]["outputs"][0]["carousel"]["items"][0]
        rendered_text = str(c1)
        assert "비밀번호·인증번호" in rendered_text
        assert "비밀번호를 입력해주세요" in rendered_text

    def test_signals_are_capped_at_max_carousel_items(self):
        signals = [_signal(evidence=f"증거{i}") for i in range(20)]
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=signals),
            _part("not_run"),
        )

        carousel = render_detail_carousel(analysis)

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == MAX_CAROUSEL_SIGNAL_ITEMS + 2
        assert len(items) <= 10

    def test_no_signals_still_produces_c2_and_c3(self):
        analysis = _analysis(
            "not_registered",
            _part("no_risk_found", brand=None),
            _part("not_run"),
        )

        carousel = render_detail_carousel(analysis)

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == _R9_DETAIL_ITEMS + 2


class TestRenderDetailCarouselGuidance:
    """이슈 #69: find_guidance() 결과를 캐러셀 끝에 공식 확인 방법
    카드로 붙인다. ai.kb.guidance가 아직 merge되지 않아 실제 함수
    대신 services.result_card_renderer.find_guidance를 monkeypatch한다."""

    def _analysis(self):
        return _analysis(
            "not_registered",
            _part("no_risk_found", brand=None),
            _part("not_run"),
        )

    def test_no_guidance_adds_no_cards(self, monkeypatch):
        monkeypatch.setattr(result_card_renderer, "find_guidance", lambda payload: [])

        carousel = render_detail_carousel(self._analysis())

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == _R9_DETAIL_ITEMS + 2  # R9 상세 + C2 + C3만

    def test_verification_kind_uses_text_as_is(self, monkeypatch):
        monkeypatch.setattr(
            result_card_renderer, "find_guidance",
            lambda payload: [_guidance(kind="verification", text="인터넷우체국에서 본인 확인 후 조회하세요.")],
        )

        carousel = render_detail_carousel(self._analysis())

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == _R9_DETAIL_ITEMS + 3
        assert "인터넷우체국에서 본인 확인 후 조회하세요." in str(items[-1])

    def test_procedure_kind_formats_source_and_year(self, monkeypatch):
        monkeypatch.setattr(
            result_card_renderer, "find_guidance",
            lambda payload: [_guidance(
                kind="procedure", text="문자 대신 공식 앱으로 확인하라",
                source="우정사업본부", published_at="2024-03-06",
            )],
        )

        carousel = render_detail_carousel(self._analysis())

        description = str(carousel["template"]["outputs"][0]["carousel"]["items"][-1])
        assert "우정사업본부는 문자 대신 공식 앱으로 확인하라라고 안내했어요(2024)." in description
        # 결론을 덧붙이지 않는다 (이슈: "그래서 이 문자는 가짜" 금지)
        assert "가짜" not in description
        assert "진짜" not in description

    def test_more_than_three_guidance_items_are_capped(self, monkeypatch):
        monkeypatch.setattr(
            result_card_renderer, "find_guidance",
            lambda payload: [_guidance(text=f"안내{i}") for i in range(5)],
        )

        carousel = render_detail_carousel(self._analysis())

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == _R9_DETAIL_ITEMS + 2 + 3  # R9 상세 + C2 + C3 + 안내 최대 3장

    def test_long_guidance_text_is_truncated_to_128_chars(self, monkeypatch):
        long_text = "고객센터 " + "0" * 200
        monkeypatch.setattr(
            result_card_renderer, "find_guidance",
            lambda payload: [_guidance(kind="verification", text=long_text, rule_id="synthetic")],
        )

        carousel = render_detail_carousel(self._analysis())

        guidance_card = carousel["template"]["outputs"][0]["carousel"]["items"][-1]
        rendered = guidance_card["description"]
        assert len(rendered) <= 128
        assert rendered.endswith("…")

    def test_guidance_items_shrink_the_signal_budget_to_stay_within_ten(self, monkeypatch):
        monkeypatch.setattr(
            result_card_renderer, "find_guidance",
            lambda payload: [_guidance(text=f"안내{i}") for i in range(3)],
        )
        signals = [_signal(evidence=f"증거{i}") for i in range(20)]
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=signals),
            _part("not_run"),
        )

        carousel = render_detail_carousel(analysis)

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == MAX_CAROUSEL_TOTAL_ITEMS

    def test_find_guidance_raising_does_not_break_the_carousel(self, monkeypatch):
        def _boom(payload):
            raise RuntimeError("kb unavailable")

        monkeypatch.setattr(result_card_renderer, "find_guidance", _boom)

        carousel = render_detail_carousel(self._analysis())

        items = carousel["template"]["outputs"][0]["carousel"]["items"]
        assert len(items) == _R9_DETAIL_ITEMS + 2  # R9 상세, C2, C3는 그대로 나간다

    def test_c2_shows_message_failure_reason_when_env_is_complete(self):
        """이슈 #59: 문자 분석 실패 + 페이지 분석 정상 완료 — 정상 완료된
        페이지 분석을 문자 분석 실패 때문에 실패로 보이게 하면 안 된다."""

        analysis = _analysis(
            "brand_mismatch",
            _part("partial", brand="CJ대한통운", failures=["timeout"]),
            _part("no_risk_found"),
        )

        c2 = _c2_text(analysis)

        assert "문자: 분석 시간이 초과되어 끝까지 확인하지 못했어요" in c2
        assert "페이지:" not in c2

    def test_c2_shows_env_reason_when_message_is_complete(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("failed"),
        )

        c2 = _c2_text(analysis)

        assert "문자:" not in c2
        assert "페이지: 페이지를 열어보려 했지만 열지 못했어요." in c2

    def test_c2_shows_empty_input_reason_for_link_only_message(self):
        """이슈 #78: 링크만 보낸 경우도 '자세히 보기'에서 기본 문구가
        아니라 전용 문구로 나와야 한다."""

        analysis = _analysis(
            "brand_mismatch",
            _part("failed", brand="CJ대한통운", failures=["empty_input"]),
            _part("no_risk_found"),
        )

        c2 = _c2_text(analysis)

        assert "문자: 링크만 있어서 확인할 글이 없었어요" in c2

    def test_c2_shows_both_reasons_distinguished_when_both_incomplete(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("failed", brand="CJ대한통운", failures=["llm_error"]),
            _part("not_run"),
        )

        c2 = _c2_text(analysis)

        assert "문자: 분석 중 오류가 발생해 확인하지 못했어요" in c2
        assert "페이지: 페이지 내용은 확인하지 않았어요." in c2

    def test_c2_falls_back_when_both_complete(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("no_risk_found"),
        )

        c2 = _c2_text(analysis)

        assert "공식 주소 대조와 문자·페이지 분석을 완료했어요." in c2
        assert "문자:" not in c2
        assert "페이지:" not in c2

    def test_c2_shows_message_incomplete_even_with_confirmed_signal(self):
        """이슈 #59 권장 검증: 검증된 위험 신호가 있어도(partial + signals)
        문자 분석 자체는 못 끝났으면 C2에 그 사실을 보여준다."""

        analysis = _analysis(
            "brand_mismatch",
            _part(
                "partial", brand="CJ대한통운",
                signals=[_signal()], failures=["timeout"],
            ),
            _part("not_run"),
        )

        carousel = render_detail_carousel(analysis)
        items = carousel["template"]["outputs"][0]["carousel"]["items"]

        assert len(items) == 1 + 1 + 1  # 신호 1장 + C2 + C3
        c2 = _c2_text(analysis)
        assert "문자: 분석 시간이 초과되어 끝까지 확인하지 못했어요" in c2


class TestK6PaymentStopLine:
    """앱 설치만 답했으면 송금을 전제한 '송금한 은행' 문장을 쓰지 않는다."""

    _APP_ACTIONS = [
        "request_payment_stop", "isolate_phone", "use_other_device", "remove_app_and_check_device",
    ]

    def _description(self, response_actions, answers):
        card = result_card_renderer.render_voice_answer_card(
            {"response_actions": response_actions}, answers,
        )
        return card["template"]["outputs"][0]["textCard"]["description"]

    def test_installed_app_only_asks_to_stop_own_account(self):
        description = self._description(self._APP_ACTIONS, ["installed_app"])

        assert description.startswith("1. 거래 은행에 계좌 지급정지를 요청하세요.")
        assert "송금한 은행" not in description

    def test_sent_money_keeps_sending_bank_line(self):
        description = self._description(["request_payment_stop", "report_112"], ["sent_money"])

        assert description.startswith("1. 송금한 은행에 지급정지를 요청하세요.")


class TestQ1Thumbnails:
    """Q1 리스트 카드 항목에는 답마다 썸네일을 단다. Q2 에는 달지 않는다."""

    _OPTIONS = ["entered_info", "installed_app", "sent_money", "not_yet"]

    @staticmethod
    def _items(card):
        return card["template"]["outputs"][0]["listCard"]["items"]

    def test_voice_q1_items_have_images(self):
        card = result_card_renderer.render_voice_qa_prompt_card(
            "job-1", {"id": "already_acted", "option_ids": self._OPTIONS},
        )

        images = [item.get("imageUrl", "") for item in self._items(card)]
        assert images[0].endswith("q1-list-1-entered-info.png")
        assert images[1].endswith("q1-list-2-installed-app.png")
        assert images[2].endswith("q1-list-3-sent-money.png")
        assert images[3].endswith("q1-list-4-not-yet.png")

    def test_message_q1_items_have_images_except_clicked_link(self):
        card = result_card_renderer.render_message_question_card(
            "job-1", {"question_id": "already_acted", "option_ids": ["clicked_link", *self._OPTIONS]},
        )

        items = self._items(card)
        assert "imageUrl" not in items[0]
        assert all(item["imageUrl"] for item in items[1:])

    def test_message_q2_items_have_no_images(self):
        card = result_card_renderer.render_message_question_card(
            "job-1", {"question_id": "called_known_number", "option_ids": ["not_person", "not_yet"]},
        )

        assert all("imageUrl" not in item for item in self._items(card))
