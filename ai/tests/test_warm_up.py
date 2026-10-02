"""서버 기동 시 사례 검색 KB 를 미리 불러오는 워밍업을 검증한다."""

import pytest

import ai.kb.search as search
from ai.pipeline import warm_up_case_search
from ai.types import AnalysisStatus


@pytest.fixture
def cold_kb():
    """다른 테스트가 채운 캐시를 비워, 워밍업이 직접 채우는지 본다."""
    search._load_cases.cache_clear()
    search._load_index.cache_clear()


def test_search_after_warm_up_does_not_reload_kb(cold_kb, monkeypatch):
    assert warm_up_case_search().status is AnalysisStatus.COMPLETED

    def forbidden(*args, **kwargs):
        raise AssertionError("워밍업 뒤 검색이 KB 를 다시 읽거나 색인을 다시 만들면 안 된다")

    monkeypatch.setattr(search, "_parse_case", forbidden)
    monkeypatch.setattr(search, "build_index", forbidden)

    result = search.search_cases("[CJ대한통운] 배송지 확인이 필요합니다.")

    assert result.status is AnalysisStatus.COMPLETED
