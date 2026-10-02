"""services.result_card_selector 테스트.

docs/designs/result-meaning-cases.md 4절의 S1~S5 상태표를 기준 사례로
삼는다. "정상 조건"은 아직 팀 미확정(6절 항목 9)이므로, 여기 기대값은
select_result_card()의 임시 규칙을 고정하기 위한 것이지 최종 합의가
아니다.
"""

from services.result_card_selector import select_result_card


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


def _analysis(official: str, message: dict, env: dict) -> dict:
    return {
        "url": {
            "final_url": "https://example.com",
            "domain": "example.com",
            "official": official,
            "scan": {"score": None, "scanned_at": None},
        },
        "message": message,
        "env": env,
        "result": False,
    }


class TestSelectResultCard:
    def test_official_and_both_no_risk_is_official_card(self):
        # S2 — docs/designs/result-meaning-cases.md 4절
        analysis = _analysis(
            "official",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("no_risk_found"),
        )

        assert select_result_card(analysis) == "r2-official"

    def test_official_but_env_not_run_is_uncertain(self):
        """지금 격리 수집기가 없어 official이어도 env는 거의 항상
        not_run/failed다 — 6절 항목 9의 잠정 답(S4 결론 줄)."""
        analysis = _analysis(
            "official",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r9-uncertain"

    def test_brand_mismatch_without_signals_is_not_official(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r8-not-official"

    def test_brand_mismatch_with_signals_is_still_lookalike(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r1-lookalike"
        
    def test_brand_mismatch_with_unknown_brand_is_uncertain(self):
        analysis = _analysis(
            "brand_mismatch",
            _part("no_risk_found", brand="unknown"),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r9-uncertain"
        
    def test_not_registered_with_known_brand_is_uncertain(self):
        analysis = _analysis(
            "not_registered",
            _part("no_risk_found", brand="누리몰"),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r9-uncertain"


    def test_not_registered_without_brand_is_uncertain(self):
        analysis = _analysis(
            "not_registered",
            _part("no_risk_found", brand=None),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r9-uncertain"

    def test_unresolved_is_uncertain(self):
        analysis = _analysis(
            "unresolved",
            _part("no_risk_found", brand=None),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r9-uncertain"

    def test_signals_in_message_override_official_state(self):
        """검증된 위험 근거는 다른 상태보다 먼저 본다
        (PR #39 멘토 피드백, result-meaning-cases.md 4절)."""
        analysis = _analysis(
            "official",
            _part("risk_found", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r1-lookalike"

    def test_partial_answer_with_signals_is_still_treated_as_risk(self):
        """answer=partial이어도 signals가 있으면 위험 근거가 있다는 뜻이다
        (docs/scheme.md). answer만 보고 버리면 안 된다."""
        analysis = _analysis(
            "official",
            _part("partial", brand="CJ대한통운", signals=[_signal()]),
            _part("not_run"),
        )

        assert select_result_card(analysis) == "r1-lookalike"

    def test_signals_in_env_also_override_official_state(self):
        analysis = _analysis(
            "official",
            _part("no_risk_found", brand="CJ대한통운"),
            _part("risk_found", signals=[_signal("credential_request", "비밀번호 입력폼")]),
        )

        assert select_result_card(analysis) == "r1-lookalike"
