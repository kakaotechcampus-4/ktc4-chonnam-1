from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest



@pytest.fixture
def make_parse_client():
    def build(parsed=None, refusal=None, side_effect=None):
        parse = AsyncMock(side_effect=side_effect)
        if side_effect is None:
            parse.return_value = SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))]
            )
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(parse=parse))
        ), parse

    return build
