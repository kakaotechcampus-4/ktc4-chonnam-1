"""BE 테스트 공용 픽스처."""

from urllib.parse import urlsplit

import pytest

import main
from ai.case import MessageRuntime, analyze_message_case
from ai.case.lexicon import read_with_lexicon
from ai.types import (
    AnalysisStatus, CaseSearchResult, FailureCode, LinkCandidate, LinkOrigin, MessageAnalysis,
    MessageReading,
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


async def _lexicon_read(source):
    # 실제 LLM 대신 어휘표로 읽는다. 시간 초과 폴백과 같은 모양이다
    return read_with_lexicon(source, FailureCode.TIMEOUT)


def _no_cases(text):
    return CaseSearchResult(status=AnalysisStatus.COMPLETED, matches=[])


OFFLINE_MESSAGE_RUNTIME = MessageRuntime(read_claims=_lexicon_read, search_cases=_no_cases)


@pytest.fixture(autouse=True)
def offline_message_case(monkeypatch):
    """링크 없는 문자 분석이 실제 LLM·임베딩을 부르지 않게 한다.

    읽기는 어휘표, 사례 검색은 빈 결과다. 테스트가 직접 바꾸려면 main.analyze_message_case 를 다시 덮는다.
    """

    async def offline(text, **kwargs):
        kwargs.setdefault("runtime", OFFLINE_MESSAGE_RUNTIME)
        return await analyze_message_case(text, **kwargs)

    monkeypatch.setattr(main, "analyze_message_case", offline)
