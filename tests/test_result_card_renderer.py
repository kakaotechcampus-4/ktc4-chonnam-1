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
)


def _part(answer: str, brand: str | None = None, signals: list | None = None) -> dict:
    return {
        "brand": brand,
        "category": None,
        "answer": answer,
        "details": {
            "doubts": [],
            "signals": signals or [],
            "reason": {"text": "x", "failures": []},
        },
    }


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
            "not_registered",
            _part("no_risk_found", brand="누리몰"),
            _part("not_run"),
        )

        card = render_result_card(analysis)

        text_card = card["template"]["outputs"][0]["textCard"]
        assert "누리몰" in text_card["description"]

    def test_job_id_is_injected_into_detail_button(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        card = render_result_card(analysis, job_id="job-123")

        text_card = card["template"]["outputs"][0]["textCard"]
        detail_button = next(
            b for b in text_card["buttons"] if b["label"] == "자세히 보기"
        )
        assert detail_button["extra"] == {"job_id": "job-123"}

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
