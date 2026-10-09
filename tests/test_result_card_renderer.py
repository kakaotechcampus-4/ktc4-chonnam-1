"""services.result_card_renderer 테스트.

select_result_card()가 고른 카드를 실제로 값까지 채워서 돌려주는지,
job_id가 "자세히 보기" 버튼에 꽂히는지, 캐러셀이 10장 상한을 지키는지를
검증한다. 카드 선택 로직 자체(언제 어떤 카드를 고르는가)는
test_result_card_selector.py의 몫이라 여기서는 다시 다루지 않는다.
"""

from services.result_card_renderer import (
    MAX_CAROUSEL_SIGNAL_ITEMS,
    merge_kakao_responses,
    render_detail_carousel,
    render_result_card,
    _r9_blank_values,
)


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

        text_card = card["template"]["outputs"][0]["textCard"]
        assert "CJ대한통운" in text_card["title"]
        buttons = {b["label"]: b for b in text_card["buttons"]}
        assert buttons["공식 홈페이지 열기"]["webLinkUrl"] == "https://cjlogistics.com"

    def test_not_official_card_fills_org_name(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("no_risk_found", brand="누리몰"),
            _part("not_run"),
        )

        card = render_result_card(analysis)

        text_card = card["template"]["outputs"][0]["textCard"]
        assert "누리몰" in text_card["description"]

    def test_no_job_id_means_no_extra_injected(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        card = render_result_card(analysis)

        text_card = card["template"]["outputs"][0]["textCard"]
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


def _r9_description(analysis: dict) -> str:
    card = render_result_card(analysis)
    return card["template"]["outputs"][0]["textCard"]["description"]


class TestR9BlankValues:
    """docs/designs/chatbot-copy.md "R9 판단 보류 → 빈칸 채우는 규칙" 표를
    그대로 검증한다. 멘토 리뷰(PR #39) 반영으로 추가된 빈칸 연동."""

    def test_org_label_uses_brand_when_known(self):
        analysis = _analysis(
            "not_registered", _part("no_risk_found", brand="CJ대한통운"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 속 링크 대신 CJ대한통운 공식 앱이나 홈페이지" in description

    def test_org_label_falls_back_when_brand_unknown(self):
        analysis = _analysis(
            "unresolved", _part("no_risk_found", brand="unknown"), _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 속 링크 대신 보낸 기관의 공식 앱이나 홈페이지" in description

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
        assert "- 문자 내용 (분석을 끝내지 못했어요)" in description

    def test_message_partial_is_only_unchecked(self):
        analysis = _analysis(
            "not_registered",
            _part("partial", brand="CJ대한통운"),
            _part("not_run"),
        )

        values = _r9_blank_values(analysis)

        assert "- 문자 내용 (분석을 끝내지 못했어요)" in values["unchecked"]
        assert "문자 내용 (분석을 끝내지 못했어요)" not in values["checked"]

    def test_message_partial_from_timeout_is_treated_as_unchecked(self):
        """멘토 리뷰(PR #54): 시간 초과는 어디까지 분석했는지 경계가 불확실해
        "일부에서 위험 신호를 찾지 못했다"고 단정하면 안 된다."""

        analysis = _analysis(
            "not_registered",
            _part("partial", brand="CJ대한통운", failures=["timeout"]),
            _part("not_run"),
        )

        description = _r9_description(analysis)

        assert "문자 일부에서 위험 신호를 찾지 못했어요" not in description
        assert "문자 나머지" not in description
        assert "- 문자 내용 (분석을 끝내지 못했어요)" in description

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
            "- 문자 내용 (분석하지 못했어요)"
            in _r9_description(failed)
        )
        assert "- 문자 내용" in _r9_description(not_run)
        assert (
            "문자 내용 (분석하지 못했어요)"
            not in _r9_description(not_run)
        )

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
        assert len(items) == 2
