import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai.types import Observations

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def load_observations():
    def _load(name: str) -> Observations:
        path = FIXTURES / f"observations_{name}.json"
        return Observations.model_validate(json.loads(path.read_text(encoding="utf-8")))

    return _load


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
