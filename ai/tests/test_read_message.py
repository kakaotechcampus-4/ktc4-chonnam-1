"""요구 분석 LLM 한 번으로 링크까지 읽는 read_message (ADR 0007)."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import ai.llm.analyze as analyze_module
import ai.pipeline.analysis as module
from ai.pipeline import analyze_message_part, read_message
from ai.types import (
    AnalysisStatus, Brand, CaseSearchResult, EvidenceField, ExtractedMessage, FailureCode,
    LinkCandidate, LinkOrigin, SignalProposal,
)

TEXT = "[CJ대한통운] 주소 불일치로 반송 예정. bit.ly/abc123 에서 주소를 수정해 주세요"
READING = ExtractedMessage(
    claimed_sender=EvidenceField(value="CJ대한통운", evidence="[CJ대한통운]"),
    requested_actions=[
        EvidenceField(value="주소 수정", evidence="주소를 수정해 주세요"),
        # 링크 글자를 담은 인용은 링크를 뗀 본문에 없어 버린다
        EvidenceField(value="링크 접속", evidence="bit.ly/abc123 에서"),
    ],
    urls=["bit.ly/abc123"],
)


class FakeClient:
    """response_format 별로 정해 둔 결과를 돌려주고 부른 순서를 남긴다."""

    def __init__(self, extracted=None, error=None):
        self.formats = []
        self.extracted = extracted
        self.error = error
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True

    async def parse(self, **kwargs):
        response_format = kwargs["response_format"]
        self.formats.append(response_format)
        if response_format is not ExtractedMessage:
            parsed = response_format()
        elif self.error is not None:
            raise self.error
        else:
            parsed = self.extracted
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(refusal=None, parsed=parsed))])


@pytest.fixture(autouse=True)
def local_search(monkeypatch):
    monkeypatch.setattr(module, "search_cases", lambda text: CaseSearchResult(
        status=AnalysisStatus.COMPLETED))


async def test_links_and_requests_come_from_one_reading():
    client = FakeClient(READING)

    reading = await read_message(TEXT, client=client, model="test")

    assert client.formats == [ExtractedMessage]
    assert reading.links == (LinkCandidate(
        url="https://bit.ly/abc123", domain="bit.ly", origin=LinkOrigin.MESSAGE),)
    assert reading.message == "[CJ대한통운] 주소 불일치로 반송 예정.  에서 주소를 수정해 주세요"
    assert reading.extracted.analysis_status is AnalysisStatus.COMPLETED
    assert reading.extracted.claimed_sender.value == "CJ대한통운"
    assert [item.evidence for item in reading.extracted.requested_actions] == ["주소를 수정해 주세요"]
    # 링크는 links 에만 둔다
    assert reading.extracted.urls == []


async def test_corrected_spelling_is_not_a_link():
    text = "[CJ대한통운] 배송 조회 cjlogistlcs.com"
    client = FakeClient(ExtractedMessage(urls=["cjlogistics.com"]))

    reading = await read_message(text, client=client, model="test")

    assert reading.links == ()
    assert reading.message == text


async def test_failed_reading_finds_no_links_and_says_so():
    client = FakeClient(error=RuntimeError("upstream"))

    reading = await read_message(TEXT, client=client, model="test")

    assert reading.links == ()
    assert reading.extracted.analysis_status is AnalysisStatus.FALLBACK
    assert reading.extracted.failure is FailureCode.LLM_ERROR
    assert reading.message == TEXT


@pytest.mark.parametrize("text, failure", [
    ("   ", FailureCode.EMPTY_INPUT),
    ("가" * (module.MAX_MESSAGE_CHARS + 1), FailureCode.INPUT_TOO_LARGE),
], ids=["blank", "too_large"])
async def test_unreadable_input_skips_model(text, failure):
    client = FakeClient(ExtractedMessage())

    reading = await read_message(text, client=client, model="test")

    assert client.formats == []
    assert reading.links == ()
    assert reading.extracted.failure is failure


@pytest.mark.parametrize("owned", [False, True])
async def test_closes_only_owned_client(owned, monkeypatch):
    client = FakeClient(READING)
    factory = Mock(return_value=client)
    monkeypatch.setattr(module, "create_client", factory)

    await read_message(TEXT, client=None if owned else client, model="test")

    assert client.closed is owned
    # 예산은 추출 단계의 것을 쓴다 (ADR 0004)
    assert factory.call_args_list == ([call(analyze_module.TIMEOUT_SECONDS)] if owned else [])


async def test_missing_client_config_is_fallback_not_error(monkeypatch):
    monkeypatch.setattr(module, "create_client", Mock(side_effect=RuntimeError("not configured")))

    reading = await read_message(TEXT, model="test")

    assert reading.links == ()
    assert reading.extracted.failure is FailureCode.LLM_ERROR


async def test_same_link_written_twice_is_one_link():
    text = "example.com 또는 https://example.com/ 에서 확인"
    client = FakeClient(ExtractedMessage(urls=["example.com", "https://example.com/"]))

    reading = await read_message(text, client=client, model="test")

    assert [link.url for link in reading.links] == ["https://example.com/"]
    assert reading.message == "또는  에서 확인"


async def test_part_reuses_reading_without_second_extraction():
    client = FakeClient(READING)
    reading = await read_message(TEXT, client=client, model="test")

    part = await analyze_message_part(
        reading.message, extracted=reading.extracted, client=client, model="test")

    # 추출은 read_message 의 한 번뿐이고, 이어서는 위험 신호 제안만 부른다
    assert client.formats == [ExtractedMessage, SignalProposal]
    assert part.brand is Brand.CJ_LOGISTICS
    assert "주소를 수정해 주세요" in part.details.reason.text


async def test_part_without_reading_still_extracts():
    client = FakeClient(ExtractedMessage())

    await analyze_message_part("[CJ대한통운] 배송 조회하세요", client=client, model="test")

    assert client.formats == [ExtractedMessage, SignalProposal]
