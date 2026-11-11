"""#157 textCard limits and #159 presentation contracts."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from services import result_card_renderer as renderer
from services import card_parts, detail_carousel
from services.chatbot_copy_data import SIGNAL_COPY


def _scope_item(items: list[dict]) -> dict:
    # D19: 판단 보류(R9)면 확인한 것, 확인하지 못한 것 장이 C2 앞에 붙는다. C2 는 제목으로 찾는다
    return next(item for item in items if item["title"] == "📋 확인 범위")


@pytest.mark.parametrize("code", list(SIGNAL_COPY))
@pytest.mark.parametrize("length", [0, 1, 59, 60, 128, 300])
def test_text_carousel_keeps_complete_reason_within_128(monkeypatch, code, length):
    monkeypatch.setattr(detail_carousel, "find_guidance", lambda _: [])
    payload = {"message": {"answer": "risk_found", "details": {
        "signals": [{"code": code, "evidence": "가" * length}],
    }}, "env": {"answer": "not_run"}}
    card = renderer.render_detail_carousel(payload)
    outputs = card["template"]["outputs"]
    assert len(outputs) == 1
    carousel = outputs[0]["carousel"]
    assert carousel["type"] == "textCard"
    assert len(carousel["items"]) <= 10
    for item in carousel["items"]:
        assert "thumbnail" not in item
        assert len(item["description"]) <= 128
    first = carousel["items"][0]
    assert first["title"] == SIGNAL_COPY[code][0]
    assert first["description"].endswith(SIGNAL_COPY[code][1])
    if length == 0:
        assert "인용문이 제공되지 않았어요." in first["description"]
        assert "문자에 ''" not in first["description"]
    elif length == 1:
        assert "문자에 '가'라고 적혀 있어요." in first["description"]
    if length == 300:
        assert "…'라고 적혀 있어요." in first["description"]


@pytest.mark.parametrize("answers, expected", [
    (["installed_app"], "거래 은행에 계좌 지급정지를 요청하세요."),
    (["sent_money"], "송금한 은행에 지급정지를 요청하세요."),
    (["entered_info", "sent_money"], "송금한 은행에 지급정지를 요청하세요."),
])
def test_k6_payment_stop_matches_answer_and_numbers_only_visible_actions(answers, expected):
    lines = card_parts.response_action_lines(
        ["unknown_action", "request_payment_stop", "report_112"], answers,
    )
    assert lines == [f"1. {expected}", "2. 112에 신고하세요."]


def test_guidance_shares_text_card_and_ten_item_budget(monkeypatch):
    monkeypatch.setattr(detail_carousel, "find_guidance", lambda _: [
        SimpleNamespace(kind="verification", text="공식 앱에서 확인하세요.",
                        source="공식 안내", published_at="2026-11-10", rule_id=str(i))
        for i in range(3)
    ])
    payload = {"message": {"answer": "risk_found", "details": {
        "signals": [{"code": "install_prompt", "evidence": "앱 설치"}] * 12,
    }}, "env": {"answer": "not_run"}}
    carousel = renderer.render_detail_carousel(payload)["template"]["outputs"][0]["carousel"]
    assert len(carousel["items"]) == 10
    assert carousel["type"] == "textCard"
    assert all("thumbnail" not in item and len(item["description"]) <= 128
               for item in carousel["items"])
    assert all(item["description"] == "공식 앱에서 확인하세요."
               for item in carousel["items"][-3:])


@pytest.mark.parametrize("message_answer", ["partial", "failed", "no_risk_found"])
@pytest.mark.parametrize("env_answer", ["partial", "failed", "not_run", "no_risk_found"])
def test_scope_with_failures_fits_without_losing_disclaimer(monkeypatch, message_answer, env_answer):
    monkeypatch.setattr(detail_carousel, "find_guidance", lambda _: [])
    part = lambda answer: {"answer": answer, "details": {
        "signals": [], "reason": {"failures": ["timeout", "collection_failed"]},
    }}
    carousel = renderer.render_detail_carousel({
        "message": part(message_answer), "env": part(env_answer),
    })["template"]["outputs"][0]["carousel"]
    scope = _scope_item(carousel["items"])
    assert len(scope["description"]) <= 128
    assert scope["description"].endswith("확인하지 못한 부분은 판단에 넣지 않았어요.")


def test_r8_basic_card_uses_existing_image_asset():
    payload = {
        "url": {"official": "brand_mismatch", "domain": "example.com"},
        "message": {"brand": "누리몰", "answer": "no_risk_found", "details": {}},
        "env": {"answer": "not_run", "details": {}},
    }
    card = renderer.render_result_card(payload)["template"]["outputs"][0]["basicCard"]
    assert "누리몰" in card["description"]
    assert card["thumbnail"]["imageUrl"].endswith("/r1-danger.png")
    assets = Path(__file__).resolve().parents[1] / "frontend/kakao-templates/assets/cards"
    assert (assets / "r1-danger.png").is_file()


@pytest.mark.parametrize("official", ["unresolved", "not_registered", None, "official", "brand_mismatch"])
@pytest.mark.parametrize("message_answer,env_answer", [
    ("risk_found", "no_risk_found"), ("partial", "failed"),
    ("failed", "partial"), (None, "no_risk_found"),
])
def test_scope_separates_address_verification_from_analysis(monkeypatch, official, message_answer, env_answer):
    monkeypatch.setattr(detail_carousel, "find_guidance", lambda _: [])
    payload = {"url": {"official": official},
               "message": {"answer": message_answer, "details": {"reason": {"failures": ["timeout"]}}},
               "env": {"answer": env_answer}}
    items = renderer.render_detail_carousel(payload)["template"]["outputs"][0]["carousel"]["items"]
    scope = _scope_item(items)["description"]
    assert len(scope) <= 128
    assert "모두 확인했어요" not in scope
    assert ("공식 주소 여부는 확인하지 못했어요." in scope) == (official not in ("official", "brand_mismatch"))
    assert ("문자:" in scope) == (message_answer not in ("risk_found", "no_risk_found"))
    assert ("페이지:" in scope) == (env_answer not in ("risk_found", "no_risk_found"))
    if official in ("official", "brand_mismatch") and message_answer == "risk_found":
        assert "공식 주소 대조와 문자·페이지 분석을 완료했어요." in scope


@pytest.mark.parametrize("evidence", [None, "", "   ", "\n\t"])
def test_missing_quote_does_not_invent_observations(monkeypatch, evidence):
    monkeypatch.setattr(detail_carousel, "find_guidance", lambda _: [])
    payload = {"message": {"answer": "partial", "details": {"signals": [
        {"code": "install_prompt", "evidence": evidence},
    ]}}, "env": {"answer": "not_run"}}
    first = renderer.render_detail_carousel(payload)["template"]["outputs"][0]["carousel"]["items"][0]
    assert "인용문이 제공되지 않았어요." in first["description"]
    assert "문자에 '" not in first["description"]
    assert first["title"] == SIGNAL_COPY["install_prompt"][0]
    assert first["description"].endswith(SIGNAL_COPY["install_prompt"][1])
    assert len(first["description"]) <= 128
