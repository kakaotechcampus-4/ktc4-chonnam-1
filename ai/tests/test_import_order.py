"""패키지 의존 방향: case -> kb, case -> llm. 거꾸로 가면 import 순서에 따라 깨진다."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["ai.kb.numbers", "ai.case.claims", "ai.case.types", "ai.llm.analyze"])
def test_module_imports_on_its_own(module):
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"], capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr[-500:]
