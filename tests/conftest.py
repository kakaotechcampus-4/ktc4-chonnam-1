"""BE 테스트 공용 픽스처."""

from urllib.parse import urlsplit

import pytest

import main
from ai.types import (
    AnalysisStatus, FailureCode, LinkCandidate, LinkOrigin, MessageAnalysis, MessageReading,
)


def make_reading(
    links=(), message="", *, unverified=False, failure: FailureCode | None = None,
) -> MessageReading:
    """LLM 없이 read_message() 결과를 만든다. failure 를 주면 추출 실패(fallback)다."""

    status = AnalysisStatus.COMPLETED if failure is None else AnalysisStatus.FALLBACK
    return MessageReading(
        links=tuple(
            LinkCandidate(url=url, domain=urlsplit(url).hostname, origin=LinkOrigin.MESSAGE)
            for url in links
        ),
        message=message,
        extracted=MessageAnalysis(analysis_status=status, failure=failure),
        unverified_links=unverified,
    )


@pytest.fixture
def stub_read_message(monkeypatch):
    """main.read_message 가 정해 둔 읽기를 돌려주게 한다. 받은 (원문, 인자) 목록을 돌려준다."""

    def install(*args, **kwargs):
        reading = make_reading(*args, **kwargs)
        calls = []

        async def fake_read_message(text, **options):
            calls.append((text, options))
            return reading

        monkeypatch.setattr(main, "read_message", fake_read_message)
        return calls

    return install
