"""C4 uses curated rules without cutting contact details or changing selection."""

import re
from types import SimpleNamespace

import pytest

from ai.kb.guidance import find_guidance, load_rules
from services import detail_carousel as renderer
from services.chatbot_copy_data import GUIDANCE_DELIVERY_CONTACTS


RULES = {rule.guidance.rule_id: rule.guidance for rule in load_rules()}


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_real_guidance_fits_128_and_preserves_procedure_source(rule_id):
    guidance = RULES[rule_id]
    text = renderer._guidance_description(guidance)
    assert 0 < len(text) <= 128
    assert not text.endswith("…")
    if guidance.kind == "procedure":
        assert guidance.source in text
        assert guidance.published_at[:4] in text
        assert guidance.text in text
    elif rule_id == "NR-0020":
        assert text == guidance.text


@pytest.mark.parametrize("brand", list(GUIDANCE_DELIVERY_CONTACTS))
def test_delivery_uses_only_the_brand_contact_from_the_real_rule(brand):
    name, number = GUIDANCE_DELIVERY_CONTACTS[brand]
    text = renderer._guidance_description(RULES["NR-0015"], brand)
    assert number in RULES["NR-0015"].text
    assert f"{name} 고객센터 {number}" in text
    assert "문자 속 링크나 걸려 온 번호 대신" in text
    assert "직접 전화해 확인하세요." in text
    assert re.findall(r"\d{4}-\d{4}", text) == [number]
    assert len(text) <= 128


@pytest.mark.parametrize("brand", [None, "unknown", "CJ익스프레스"])
def test_unknown_delivery_keeps_all_five_complete_numbers(brand):
    text = renderer._guidance_description(RULES["NR-0015"], brand)
    expected = re.findall(r"\d{4}-\d{4}", RULES["NR-0015"].text)
    assert re.findall(r"\d{4}-\d{4}", text) == expected
    assert "직접 전화해 확인하세요." in text
    assert len(text) <= 128


def test_card_rule_keeps_safe_contact_action_without_guessing_card_issuer():
    text = renderer._guidance_description(RULES["NR-0019"])
    assert "문자에 적힌 번호 대신" in text
    assert "카드사 고객센터에 직접 전화해 확인하세요." in text
    assert "카드 뒷면이나 카드사 공식 앱" in text
    assert not re.search(r"\d", text)
    assert len(text) <= 128


def test_real_lookup_keeps_card_order_count_and_ten_item_budget():
    payload = {"url": {"official": "unresolved"}, "message": {
        "brand": "로젠택배", "category": "택배", "answer": "risk_found",
        "details": {"signals": [{"code": "install_prompt", "evidence": "앱 설치"}] * 15},
    }, "env": {"answer": "not_run"}}
    guidance = find_guidance(payload)
    assert [g.rule_id for g in guidance] == ["NR-0015", "NR-0014"]
    response = renderer.render_detail_carousel(payload)
    assert len(response["template"]["outputs"]) == 1
    carousel = response["template"]["outputs"][0]["carousel"]
    assert carousel["type"] == "textCard"
    assert len(carousel["items"]) == 10
    expected = [renderer._guidance_description(g, "로젠택배") for g in guidance]
    assert [item["description"] for item in carousel["items"][-len(guidance):]] == expected
    assert all(len(item["description"]) <= 128 for item in carousel["items"])


@pytest.mark.parametrize("contact", ["1588-1234", "https://help.example/report"])
def test_unknown_long_rule_never_splits_contact_at_cutoff(contact):
    guidance = SimpleNamespace(kind="verification", rule_id="synthetic", text="가" * 124 + contact)
    text = renderer._guidance_description(guidance)
    assert text == "가" * 124 + "…"
    assert len(text) <= 128


@pytest.mark.parametrize("contact", ["1588-1234", "https://help.example/report"])
def test_unknown_rule_preserves_complete_front_loaded_action_and_contact(contact):
    action = f"{contact}로 신고·상담하세요."
    guidance = SimpleNamespace(kind="verification", rule_id="synthetic", text=action + " 부가 설명" * 50)
    text = renderer._guidance_description(guidance)
    assert action in text
    assert len(text) <= 128


@pytest.mark.parametrize("length", [127, 128, 129])
def test_unknown_rule_length_boundary(length):
    text = renderer._guidance_description(SimpleNamespace(
        kind="verification", rule_id="synthetic", text="가" * length,
    ))
    assert len(text) <= 128
    assert text.endswith("…") == (length > 128)
