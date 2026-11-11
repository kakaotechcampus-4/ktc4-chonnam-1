"""#89: 링크 결과가 판단 보류일 때 문자 게이트가 주의라고 하면 r6b-uncertain 주의 갈래로 보낸다.

새 카드는 만들지 않는다. 링크 없는 문자 결과 카드(r6b-uncertain)를 그대로 쓴다.
판정(url.official, 위험 신호)은 바꾸지 않고 판단 보류 카드의 갈래만 고른다.
"""

import pytest

import main
from ai.case.types import ClaimView, Conclusion, MessageCaseResult, Scenario, StopAction
from scanner.models import CollectResult
from services.result_card_renderer import (
    link_caution_applies,
    render_detail_carousel,
    render_link_caution_card,
)


def _part(answer: str, brand: str | None = None, signals: list | None = None) -> dict:
    return {
        "brand": brand, "category": None, "answer": answer,
        "details": {"doubts": [], "signals": signals or [], "reason": {"text": "x", "failures": []}},
    }


def _analysis(official: str, message: dict | None = None, env: dict | None = None) -> dict:
    return {
        "url": {"final_url": None, "domain": "evil.top", "official": official,
                "scan": {"score": None, "scanned_at": None}},
        "message": message or _part("no_risk_found"),
        "env": env or {"answer": "failed", "details": {"doubts": [], "signals": [],
                                                       "reason": {"text": "x", "failures": ["collection_failed"]}}},
        "result": False,
    }


def _case(caution: bool, *, conclusion: Conclusion = Conclusion.INCONCLUSIVE) -> dict:
    return MessageCaseResult(
        scenario=Scenario.LINK_SMISHING,
        conclusion=conclusion,
        caution=caution,
        stop_actions=[StopAction.DONT_CLICK_LINK, StopAction.DONT_ENTER_INFO] if caution else [StopAction.DONT_CLICK_LINK],
        claims=ClaimView(
            identity=[{"kind": "institution", "name": "청소행정과", "quote": "청소행정과입니다", "listed_institution": False}],
            pretexts=["쓰레기 무단투기 신고가 접수되었습니다"],
            demands=[{"kind": "personal_info", "quote": "정보를 입력해 주세요"}],
        ),
        checked=["message_text"],
        unchecked=[{"item": "actual_sender", "reason": "cannot_verify"}],
        explanation="x",
    ).model_dump(mode="json")


def _card_body(card: dict) -> dict:
    """카드 본체. D19 로 주의 갈래 카드는 basicCard 다."""
    output = card["template"]["outputs"][0]
    return output.get("textCard") or output["basicCard"]


class TestLinkCautionApplies:
    @pytest.mark.parametrize("official", ["not_registered", "unresolved"])
    def test_uncertain_non_official_link_asks_gate(self, official):
        assert link_caution_applies(_analysis(official)) is True

    def test_official_link_is_excluded(self):
        # #89 "공식 주소 일치는 제외": 진짜 택배사 문자에 경고가 나가지 않게 한다
        assert link_caution_applies(_analysis("official", env=_part("partial"))) is False

    def test_verified_signal_keeps_danger_card(self):
        risky = _part("risk_found", signals=[{"code": "credential_request", "evidence": "x"}])
        assert link_caution_applies(_analysis("not_registered", message=risky)) is False

    def test_lookalike_keeps_not_official_card(self):
        assert link_caution_applies(_analysis("brand_mismatch", message=_part("no_risk_found", brand="CJ대한통운"))) is False


class TestRenderLinkCautionCard:
    def test_caution_uses_r6b_uncertain_with_link_stop_line(self):
        card = render_link_caution_card(_analysis("not_registered"), _case(True))

        body = _card_body(card)
        assert body["title"] == "🔎 주의가 필요한 문자예요"
        description = body["description"]
        assert description.startswith("링크를 누르거나 정보를 입력하지 마세요.")
        assert len(description) <= 230
        # D19: 주장과 확인 범위는 '자세히 보기'(R9 와 같은 문자 캐러셀)에 있다
        items = render_detail_carousel(_analysis("not_registered"), _case(True))
        detail = str(items["template"]["outputs"][0]["carousel"]["items"])
        assert "주의할 점" in detail
        assert "- 내세운 명분: '쓰레기 무단투기 신고가 접수되었습니다'" in detail
        # 링크 결과의 주소·페이지 확인 범위를 함께 보여 준다
        assert "주소가 공식 주소인지 (보낸 기관을 몰라 대조하지 못했어요)" in detail
        assert "페이지 내용 (열지 못했어요)" in detail
        assert "- 문자 내용" in detail

    def test_no_question_cards_on_link_result(self):
        # 링크 결과 아래 Q1 은 미구현(chatbot-copy.md D16)이라 붙이지 않는다
        card = render_link_caution_card(_analysis("not_registered"), _case(True))
        assert len(card["template"]["outputs"]) == 1

    @pytest.mark.parametrize("case", [
        lambda: _case(False),
        lambda: {**_case(True), "conclusion": "input_required"},
        lambda: {**_case(True), "conclusion": "not_analyzable"},
        lambda: None,
    ])
    def test_otherwise_returns_none(self, case):
        assert render_link_caution_card(_analysis("not_registered"), case()) is None


@pytest.fixture
def link_flow(monkeypatch):
    seen = {"case": []}

    async def collect(link):
        return CollectResult(link, None, (), None, None, "", None, 1, ("timeout",))

    async def analyze(text, **kwargs):
        raise RuntimeError("message part not under test")

    def set_case(caution):
        async def case(text, **kwargs):
            seen["case"].append((text, kwargs))
            return MessageCaseResult.model_validate(_case(caution))
        monkeypatch.setattr(main, "analyze_message_case", case)

    monkeypatch.setattr(main, "collect_url_isolated", collect)
    monkeypatch.setattr(main, "analyze_message_part", analyze)
    seen["set_case"] = set_case
    return seen


async def test_uncertain_link_with_caution_sends_caution_card(stub_read_message, link_flow):
    stub_read_message(["https://evil.top/a"], "청소행정과입니다. 정보를 입력해 주세요")
    link_flow["set_case"](True)

    response = await main.run_analysis("원문 https://evil.top/a")

    assert _card_body(response)["title"] == "🔎 주의가 필요한 문자예요"
    text, kwargs = link_flow["case"][0]
    # 링크를 뗀 본문을 링크가 있는 문자로 읽는다
    assert text == "청소행정과입니다. 정보를 입력해 주세요"
    assert kwargs["has_link"] is True


async def test_uncertain_link_without_caution_keeps_r9(stub_read_message, link_flow):
    stub_read_message(["https://evil.top/a"], "배송 조회는 아래 링크에서 하세요")
    link_flow["set_case"](False)

    response = await main.run_analysis("원문 https://evil.top/a")

    assert _card_body(response)["title"] == "🤔 판단하기 어려워요"


async def test_gate_runs_once_for_several_links(stub_read_message, link_flow):
    stub_read_message(["https://evil.top/a", "https://evil2.top/b"], "정보를 입력해 주세요")
    link_flow["set_case"](True)

    await main.run_analysis("원문")

    assert len(link_flow["case"]) == 1


async def test_gate_failure_falls_back_to_r9(stub_read_message, link_flow, monkeypatch):
    stub_read_message(["https://evil.top/a"], "정보를 입력해 주세요")

    async def broken(text, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "analyze_message_case", broken)

    response = await main.run_analysis("원문 https://evil.top/a")

    assert _card_body(response)["title"] == "🤔 판단하기 어려워요"


def test_same_quote_is_not_repeated_as_institution_and_pretext():
    # LLM 이 한 문장을 기관과 명분 양쪽으로 읽어도 주의할 점에 같은 인용을 두 번 쓰지 않는다
    case = _case(True)
    quote = "kyc (Net****pi) 일시동결 되었습니다"
    case["claims"]["identity"] = [{"kind": "institution", "name": "kyc", "quote": quote, "listed_institution": False}]
    case["claims"]["pretexts"] = [quote]

    description = _card_body(render_link_caution_card(_analysis("not_registered"), case))["description"]

    assert description.count(quote) == 1
