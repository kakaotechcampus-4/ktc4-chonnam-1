import json

import httpx
import pytest

from ai.case.privacy import EliceMasker, mask_pii, mask_transcript
from ai.case.types import Segment, Side, TranscriptResult


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("주민번호 900101-1234567 입니다", "주민번호 <주민번호> 입니다"),
        ("9001011234567", "<주민번호>"),
        ("카드 1234-5678-9012-3456 불러", "카드 <카드번호> 불러"),
        ("카드 1234 5678 9012 3456", "카드 <카드번호>"),
        ("계좌 110-123-456789 로", "계좌 <계좌번호> 로"),
        ("계좌 100212345678", "계좌 <계좌번호>"),
    ],
)
def test_masks_sensitive_numbers(raw, expected):
    assert mask_pii(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        # whisper 가 숫자 묶음 사이에 공백·점을 넣어 받아 적은 모양
        ("주민번호 900101 1234567 입니다", "주민번호 <주민번호> 입니다"),
        ("주민번호 900101.1234567", "주민번호 <주민번호>"),
        ("계좌 110 123 456789 로", "계좌 <계좌번호> 로"),
        ("계좌 1234 5678 9012 입니다", "계좌 <계좌번호> 입니다"),
        ("카드 1234.5678.9012.3456", "카드 <카드번호>"),
        # 실제 접두가 아닌 번호 모양은 전화로 보지 않는다
        ("번호 012-3456-7890", "번호 <계좌번호>"),
    ],
)
def test_masks_stt_style_numbers(raw, expected):
    assert mask_pii(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "02-1234-5678로 전화하세요",
        "010-1234-5678",
        "1332에 문의",
        "1588-1234",
        "https://example.com/a?b=1234567890",
        "http://sp-case.com 에 들어가세요",
        "02-1234-5678 010-1234-5678",
        "1332 1588-1234 02-1234-5678",
        "010-1234-5678 02-1234-5678 1588-1234",
        "010 1234 5678 으로 전화하세요",
        "02 1234 5678",
        "010 1234 5678 010 1234 5678",
        "070-1234-5678",
        "0505-123-4567",
    ],
)
def test_keeps_phone_and_url(raw):
    assert mask_pii(raw) == raw


def test_mask_transcript_masks_each_segment():
    t = TranscriptResult(
        segments=[
            Segment(side=Side.UNKNOWN, text="주민번호 900101-1234567"),
            Segment(side=Side.UNKNOWN, text="02-1234-5678"),
        ],
    )
    out = mask_transcript(t)
    assert out.segments[0].text == "주민번호 <주민번호>"
    assert out.segments[1].text == "02-1234-5678"
    assert t.segments[0].text == "주민번호 900101-1234567"  # 원본 유지


@pytest.mark.asyncio
async def test_elice_masker_request_and_mapping():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=[
                {"original_text": "a", "masked_text": "A"},
                {"original_text": "b", "masked_text": "B"},
            ],
        )

    m = EliceMasker(
        "https://mask.example/", "KEY", 3.0, transport=httpx.MockTransport(handler)
    )
    assert await m.mask(["a", "b"]) == ["A", "B"]
    assert seen["url"] == "https://mask.example/mask"
    assert seen["auth"] == "Bearer KEY"
    assert seen["body"] == {"text": ["a", "b"]}


@pytest.mark.parametrize(
    "raw,expected",
    [
        (
            "02-1234-5678 로 걸고 카드 1234-5678-9012-3456 불러",
            "02-1234-5678 로 걸고 카드 <카드번호> 불러",
        ),
        (
            "010-1234-5678 이고 계좌는 110-123-456789 입니다",
            "010-1234-5678 이고 계좌는 <계좌번호> 입니다",
        ),
        (
            "1332 또는 1588-1234, 카드 1234 5678 9012 3456",
            "1332 또는 1588-1234, 카드 <카드번호>",
        ),
    ],
)
def test_mixed_numbers_mask_only_sensitive(raw, expected):
    assert mask_pii(raw) == expected
