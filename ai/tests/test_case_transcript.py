"""transcribe_call: 받아쓰기 + 화자 고르기 (스펙 D1). 실제 네트워크·LLM 은 부르지 않는다."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import yaml

from ai.case import TranscribeRuntime, VoiceRuntime, analyze_call, transcribe_call
from ai.case.transcript import split_sentences
from ai.llm import diarize as diarize_llm
from ai.llm import transcribe as transcribe_llm
from ai.types import FailureCode

from test_case_voice import CASES_DIR, Calls, _runtime  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("STT_BASE_URL", "https://stt.example/")
    monkeypatch.setenv("DIARIZE_BASE_URL", "https://llm.example")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.delenv("STT_MODEL", raising=False)
    monkeypatch.delenv("DIARIZE_MODEL", raising=False)


def _rt(text="", labels=None, *, stt_exc=None, diarize_exc=None):
    sent: list[list[str]] = []

    async def stt(audio, filename):
        if stt_exc:
            raise stt_exc
        return text

    async def dia(sentences):
        sent.append(sentences)
        if diarize_exc:
            raise diarize_exc
        return labels or {}

    rt = TranscribeRuntime(transcribe=stt, diarize=dia)
    rt.sent = sent  # type: ignore[attr-defined]
    return rt


def _run(rt, audio=b"x", name="a.m4a"):
    return asyncio.run(transcribe_call(audio, name, runtime=rt))


def test_two_speakers_merged():
    rt = _rt(
        "여보세요. 검찰입니다. 계좌가 범죄에 연루됐습니다. 네? 정말요.",
        {0: "화자2", 1: "화자2", 2: "화자2", 3: "화자1", 4: "화자1"},
    )
    r = _run(rt)
    assert [(s.speaker, s.side.value) for s in r.segments] == [
        ("화자2", "unknown"),
        ("화자1", "unknown"),
    ]
    assert r.segments[0].text == "여보세요. 검찰입니다. 계좌가 범죄에 연루됐습니다."
    assert r.segments[0].start is None and r.segments[0].end is None
    assert not r.speaker_split_failed and r.failure is None
    assert r.stt_model == "whisper-large-v3" and r.diarize_model == "claude-sonnet-5-5"


def test_empty_transcript_is_normal_and_skips_diarize():
    rt = _rt("  ")
    r = _run(rt)
    assert r.segments == [] and r.failure is None and not r.speaker_split_failed
    assert rt.sent == []


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
    assert rt.sent == []


def test_stt_wait_for_timeout(monkeypatch):
    import ai.case.transcript as t

    monkeypatch.setattr(t, "STT_TIMEOUT_SECONDS", 0.05)

    async def slow(audio, filename):
        await asyncio.sleep(1)
        return "x"

    r = _run(TranscribeRuntime(transcribe=slow, diarize=_rt().diarize))
    assert r.failure is FailureCode.TIMEOUT


def test_diarize_failure_keeps_transcript():
    r = _run(_rt("가나다. 라마바.", diarize_exc=httpx.ReadTimeout("x")))
    assert r.speaker_split_failed is True and r.failure is None
    assert [s.speaker for s in r.segments] == ["기타"]
    assert r.segments[0].text == "가나다. 라마바."


def test_diarize_wait_for_timeout(monkeypatch):
    import ai.case.transcript as t

    monkeypatch.setattr(t, "DIARIZE_TIMEOUT_SECONDS", 0.05)

    async def slow(sentences):
        await asyncio.sleep(1)
        return {}

    r = _run(TranscribeRuntime(transcribe=_rt("가나다.").transcribe, diarize=slow))
    assert r.speaker_split_failed is True and r.segments[0].text == "가나다."


def test_single_speaker_is_split_failed():
    r = _run(_rt("하나. 둘. 셋.", {0: "화자1", 1: "화자1", 2: "화자1"}))
    assert r.speaker_split_failed is True


def test_missing_ids_become_unknown_and_count_toward_ratio():
    text = "a1. b2. c3. d4. e5. f6. g7. h8. i9. j10."
    ok = {i: "화자1" if i % 2 else "화자2" for i in range(10)}
    del ok[9]  # 10% 누락
    r = _run(_rt(text, ok))
    assert r.segments[-1].speaker == "기타" and not r.speaker_split_failed
    many = {i: "화자1" if i % 2 else "화자2" for i in range(6)}  # 40% 누락
    assert _run(_rt(text, many)).speaker_split_failed is True


def test_unknown_exactly_30_percent_passes():
    text = "a1. b2. c3. d4. e5. f6. g7. h8. i9. j10."
    labels = {i: "화자1" if i % 2 else "화자2" for i in range(7)}
    assert _run(_rt(text, labels)).speaker_split_failed is False


def test_repeated_hallucination_collapsed():
    assert split_sentences("감사합니다. " * 6 + "끝.") == ["감사합니다.", "끝."]
    assert len(split_sentences("네. " * 5)) == 5  # 5번까지는 그대로
    r = _run(_rt("안녕하세요. " + "시청해 주셔서 감사합니다. " * 8, {0: "화자1", 1: "화자2"}))
    assert rt_sentences_len(r) == 2


def rt_sentences_len(r):
    return sum(s.text.count(".") for s in r.segments)


def test_pii_masked_before_llm_and_in_segments():
    rt = _rt("제 주민번호는 900101-1234567 입니다. 네.", {0: "화자1", 1: "화자2"})
    r = _run(rt)
    sent = " ".join(rt.sent[0])
    assert "900101" not in sent and "<주민번호>" in sent
    assert all("900101" not in s.text for s in r.segments)


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
        _run(TranscribeRuntime(transcribe=boom, diarize=_rt().diarize))


# ── HTTP 계층 (MockTransport) ────────────────────────────────


def _sse(*texts: str) -> str:
    events = [{"type": "message_start", "message": {}}]
    events += [
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}
        for t in texts
    ]
    events.append({"type": "message_stop"})
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events)


def _diarize_client(body: str, status=200, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_sse_parsing_with_noise_around_json():
    seen: list[httpx.Request] = []
    body = _sse("알겠습니다. ", '{"lines": [{"id": 0, "spea', 'ker": "화자1"}, {"id": 1, "speaker": "화자2"}]}', " 끝")
    client = _diarize_client(body, seen=seen)
    labels = asyncio.run(diarize_llm.pick_speakers(["하나.", "둘."], client=client))
    assert labels == {0: "화자1", 1: "화자2"}
    req = seen[0]
    assert str(req.url) == "https://llm.example/v1/messages"
    assert req.headers["authorization"] == "Bearer test-key"
    assert req.headers["anthropic-version"] == "2023-06-01"
    payload = json.loads(req.content)
    assert payload["stream"] is True and payload["max_tokens"] == 16000
    assert payload["model"] == "claude-sonnet-5-5"
    assert payload["output_config"] == {"effort": "medium"}
    assert "0. 하나." in payload["messages"][0]["content"]


def test_diarize_invalid_json_and_bad_speaker_and_http_error():
    for body in (_sse("no json here"), _sse('{"lines": [{"id": 0, "speaker": "화자3"}]}')):
        with pytest.raises(ValueError):
            asyncio.run(diarize_llm.pick_speakers(["가."], client=_diarize_client(body)))
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(diarize_llm.pick_speakers(["가."], client=_diarize_client("", 500)))


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


def test_result_feeds_analyze_call_v1_suspected():
    case = yaml.safe_load((CASES_DIR / "V1.yaml").read_text(encoding="utf-8"))
    lines = [(sp, t if t.rstrip()[-1] in ".?!…" else t + ".") for sp, t in case["transcript"]]
    names: dict[str, str] = {}
    n = 0
    labels: dict[int, str] = {}
    for sp, t in lines:
        names.setdefault(sp, f"화자{len(names) + 1}")
        for _ in split_sentences(t):
            labels[n] = names[sp]
            n += 1
    assert len(names) == 2
    # 읽기 가짜가 가리키는 상대 화자도 받아쓰기가 붙인 이름으로 맞춘다
    case["runtime"]["reading"]["other_speaker"] = names[case["runtime"]["reading"]["other_speaker"]]
    rt = _rt(" ".join(t for _, t in lines), labels)
    transcript = _run(rt)
    assert not transcript.speaker_split_failed and transcript.failure is None
    result = asyncio.run(
        analyze_call(transcript, runtime=_runtime(case, Calls()))
    )
    assert result.conclusion.value == case["expect"]["conclusion"] == "suspected"
