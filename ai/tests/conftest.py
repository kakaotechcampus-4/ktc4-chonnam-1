import json
from pathlib import Path

import pytest

from ai.types import Observations

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def load_observations():
    def _load(name: str) -> Observations:
        path = FIXTURES / f"observations_{name}.json"
        return Observations.model_validate(json.loads(path.read_text(encoding="utf-8")))

    return _load
