"""transcribe_call: 받아쓰기 + 문장 자르기 (스펙 D1, ADR 0008). 실제 네트워크·LLM 은 부르지 않는다."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import yaml

from ai.case import TranscribeRuntime, VoiceRuntime, analyze_call, transcribe_call
from ai.case.transcript import split_sentences
from ai.llm import transcribe as transcribe_llm
from ai.types import FailureCode

from test_case_voice import CASES_DIR, Calls, _runtime  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STT_BASE_URL", "https://stt.example/")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("STT_MODEL", raising=False)


def _rt(text="", *, stt_exc=None):
    async def stt(audio, filename):
        if stt_exc:
            raise stt_exc
        return text

    return TranscribeRuntime(transcribe=stt)


def _run(rt, audio=b"x", name="a.m4a"):
    return asyncio.run(transcribe_call(audio, name, runtime=rt))


def test_one_segment_per_sentence_without_speaker():
    r = _run(_rt("여보세요. 검찰입니다. 계좌가 범죄에 연루됐습니다. 네? 정말요."))
    assert [s.text for s in r.segments] == [
        "여보세요.",
        "검찰입니다.",
        "계좌가 범죄에 연루됐습니다.",
        "네?",
        "정말요.",
    ]
    assert all(s.side.value == "unknown" for s in r.segments)
    assert r.segments[0].start is None and r.segments[0].end is None
    assert r.failure is None
    assert r.stt_model == "whisper-large-v3"


def test_no_speaker_fields_in_result():
    # 화자 분리를 하지 않는다. 결과 모양에도 화자 칸이 없다 (ADR 0008)
    dumped = _run(_rt("가나다. 라마바.")).model_dump()
    assert "speaker_split_failed" not in dumped and "diarize_model" not in dumped
    assert all("speaker" not in seg for seg in dumped["segments"])


def test_runtime_has_no_diarize_hook():
    assert not hasattr(TranscribeRuntime(), "diarize")


def test_empty_transcript_is_normal():
    r = _run(_rt("  "))
    assert r.segments == [] and r.failure is None


@pytest.mark.parametrize(
    "exc, code",
    [
        (httpx.ConnectError("x"), FailureCode.LLM_ERROR),
        (httpx.ReadTimeout("x"), FailureCode.TIMEOUT),
        (ValueError("bad"), FailureCode.INVALID_OUTPUT),
    ],
)
def test_stt_failure(exc, code):
    rt = _rt(stt_exc=exc)
    r = _run(rt)
    assert r.segments == [] and r.failure is code


def test_stt_wait_for_timeout(monkeypatch):
    import ai.case.transcript as t

    monkeypatch.setattr(t, "STT_TIMEOUT_SECONDS", 0.05)

    async def slow(audio, filename):
        await asyncio.sleep(1)
        return "x"

    r = _run(TranscribeRuntime(transcribe=slow))
    assert r.failure is FailureCode.TIMEOUT


def test_repeated_hallucination_collapsed():
    assert split_sentences("감사합니다. " * 6 + "끝.") == ["감사합니다.", "끝."]
    assert len(split_sentences("네. " * 5)) == 5  # 5번까지는 그대로
    r = _run(_rt("안녕하세요. " + "시청해 주셔서 감사합니다. " * 8))
    assert len(r.segments) == 2


def test_pii_masked_in_segments():
    r = _run(_rt("제 주민번호는 900101-1234567 입니다. 네."))
    assert all("900101" not in s.text for s in r.segments)
    assert "<주민번호>" in r.segments[0].text


@pytest.mark.parametrize("rrn", ["900101.1234567", "900101 1234567"])
def test_dotted_rrn_masked_and_one_sentence(rrn):
    r = _run(_rt(f"주민번호는 {rrn} 입니다."))
    assert len(r.segments) == 1
    assert "900101" not in r.segments[0].text and "<주민번호>" in r.segments[0].text


def test_dotted_numbers_not_split():
    assert len(split_sentences("010.1234.5678 입니다. 1234.5678.9012.3456 네.")) == 2


def test_truncated_wav_header_does_not_raise(monkeypatch):
    import struct
    import wave

    def boom(*a, **k):
        raise struct.error("truncated")

    monkeypatch.setattr(wave, "open", boom)
    assert _run(_rt(""), b"RIFF\x00\x00", "a.wav").audio_seconds is None
    assert _run(_rt(""), b"RIFF\x00\x00WAVEfmt ", "a.wav").audio_seconds is None


def test_audio_seconds_for_wav_only():
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 16000)
    assert _run(_rt(""), buf.getvalue(), "a.wav").audio_seconds == 2.0
    assert _run(_rt(""), b"not audio").audio_seconds is None


def test_cancellation_propagates():
    async def boom(audio, filename):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        _run(TranscribeRuntime(transcribe=boom))


# ── HTTP 계층 (MockTransport) ────────────────────────────────


@pytest.mark.parametrize("body", [{"text": "안녕."}, {"transcript": {"text": "안녕."}}])
def test_whisper_request_and_both_response_shapes(body):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    text = asyncio.run(transcribe_llm.transcribe_audio(b"AUDIO", "call.m4a", client=client))
    assert text == "안녕."
    req = seen[0]
    assert str(req.url) == "https://stt.example/v1/audio/transcriptions"
    assert req.headers["authorization"] == "Bearer test-key"
    raw = req.content
    assert b"whisper-large-v3" in raw and b"korean" in raw and b"AUDIO" in raw
    assert b'filename="call.m4a"' in raw and b"Content-Type: audio/" in raw


@pytest.mark.parametrize(
    "base", ["https://mlapi.run/stt-id", "https://mlapi.run/stt-id/"]
)
def test_base_url_is_endpoint_root_without_v1(monkeypatch, base):
    """BE 연동 지침의 형식: STT_BASE_URL 은 /v1 없이 넣는다. 코드가 /v1/... 를 붙인다."""
    monkeypatch.setenv("STT_BASE_URL", base)
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"text": "안녕."})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    asyncio.run(transcribe_llm.transcribe_audio(b"x", "a.m4a", client=client))

    assert [str(r.url) for r in seen] == ["https://mlapi.run/stt-id/v1/audio/transcriptions"]


def test_whisper_empty_ok_and_bad_shape():
    def make(body):
        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))

    assert asyncio.run(transcribe_llm.transcribe_audio(b"x", "a.wav", client=make({"text": ""}))) == ""
    with pytest.raises(ValueError):
        asyncio.run(transcribe_llm.transcribe_audio(b"x", "a.wav", client=make({"oops": 1})))


def test_missing_env_is_stt_failure_not_exception(monkeypatch):
    monkeypatch.delenv("STT_BASE_URL")
    r = asyncio.run(transcribe_call(b"x", "a.m4a"))
    assert r.segments == [] and r.failure is FailureCode.LLM_ERROR


# ── analyze_call 연결 ────────────────────────────────────────


@pytest.mark.parametrize("reading", ["llm", "lexicon"])
def test_result_feeds_analyze_call_v1_suspected(reading):
    case = yaml.safe_load((CASES_DIR / "V1.yaml").read_text(encoding="utf-8"))
    if reading == "lexicon":
        case["runtime"]["reading"] = None  # 읽기 시간 초과 -> 어휘표
    lines = [t if t.rstrip()[-1] in ".?!…" else t + "." for t in case["transcript"]]
    transcript = _run(_rt(" ".join(lines)))
    assert transcript.failure is None
    assert len(transcript.segments) == sum(len(split_sentences(t)) for t in lines)
    result = asyncio.run(analyze_call(transcript, runtime=_runtime(case, Calls())))
    assert result.conclusion.value == case["expect"]["conclusion"] == "suspected"
