"""services.kakao_card_renderer 테스트."""

import pytest

from services.kakao_card_renderer import (
    CardNotFoundError,
    load_card_template,
    render_card,
)


class TestRenderCard:
    def test_card_without_placeholders_loads_as_is(self):
        card = render_card("r6-input-required")

        assert card["version"] == "2.0"
        assert "template" in card

    def test_nonexistent_card_raises(self):
        with pytest.raises(CardNotFoundError):
            render_card("no-such-card")

    def test_whole_string_placeholder_is_substituted(self):
        card = render_card(
            "r2-official",
            {"org_name": "CJ대한통운", "official_url": "https://cjlogistics.com"},
        )

        text_card = card["template"]["outputs"][0]["textCard"]
        assert "CJ대한통운" in text_card["title"]
        buttons = {b["label"]: b for b in text_card["buttons"]}
        assert buttons["공식 홈페이지 열기"]["webLinkUrl"] == "https://cjlogistics.com"

    def test_unprovided_placeholder_is_left_untouched(self):
        """FE가 나중에 채울 {{ block_xxx }}는 값이 없으면 그대로 둔다."""
        card = render_card("r2-official", {"org_name": "CJ대한통운"})

        text_card = card["template"]["outputs"][0]["textCard"]
        block_button = next(
            b for b in text_card["buttons"] if b["label"] == "다른 문자 확인하기"
        )
        assert block_button["blockId"] == "{{ block_b1 }}"

    def test_missing_values_do_not_crash(self):
        """값을 하나도 안 줘도 placeholder가 그대로 남을 뿐 예외는 없다."""
        card = render_card("r2-official")

        assert card["version"] == "2.0"

    def test_original_file_is_not_mutated_between_calls(self):
        first = render_card("r2-official", {"org_name": "첫번째택배"})
        second = render_card("r2-official", {"org_name": "두번째택배"})

        first_title = first["template"]["outputs"][0]["textCard"]["title"]
        second_title = second["template"]["outputs"][0]["textCard"]["title"]

        assert "첫번째택배" in first_title
        assert "두번째택배" in second_title
        assert first_title != second_title

    def test_mutating_returned_dict_does_not_affect_next_render(self):
        rendered = render_card("r6-input-required")
        rendered["template"]["outputs"][0]["simpleText"]["text"] = "망가뜨려봄"

        fresh = render_card("r6-input-required")

        assert fresh["template"]["outputs"][0]["simpleText"]["text"] != "망가뜨려봄"


class TestLoadCardTemplate:
    def test_returns_raw_json_without_substitution(self):
        template = load_card_template("r2-official")

        title = template["template"]["outputs"][0]["textCard"]["title"]
        assert "{{ org_name }}" in title
