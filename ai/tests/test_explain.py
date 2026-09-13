from ai.types import ReasonCode, Verdict
from ai.llm.explain import generate_explanation


def test_all_reason_codes_have_template():
    for code in ReasonCode:
        text = generate_explanation(Verdict(reason_code=code))
        assert text and isinstance(text, str)
