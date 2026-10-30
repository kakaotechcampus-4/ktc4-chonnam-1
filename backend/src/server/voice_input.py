"""Bounded byte/header inspection before STT, without network or codec dependencies.

This identifies audio/container evidence, not whether speech is intelligible or a
complete codec decode succeeds. Only PCM WAV duration is available here; adding
an all-format duration probe requires a separate runtime decision.
"""

import io
import math
import struct
import wave


class VoiceInputRejected(Exception):
    def __init__(self, card: str, reason_line: str = ""):
        super().__init__("voice input rejected")
        self.card = card
        self.reason_line = reason_line


def invalid_audio(reason: str) -> VoiceInputRejected:
    return VoiceInputRejected("ve2-invalid-audio", reason)


def _mp3_frame(data: bytes, offset: int = 0) -> bool:
    if len(data) < offset + 4:
        return False
    a, b, c, _ = data[offset:offset + 4]
    # MPEG sync, valid version/layer, non-reserved bitrate and sample rate.
    if not (a == 0xFF and b & 0xE0 == 0xE0 and (b >> 3) & 3 != 1
            and (b >> 1) & 3 != 0 and (c >> 4) not in (0, 15) and (c >> 2) & 3 != 3):
        return False
    version, layer = (b >> 3) & 3, (b >> 1) & 3
    # Complete MPEG Layer III frame, rather than a tag or sync bytes alone.
    if layer != 1:
        return False
    bitrates = ((32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
                if version == 3 else (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160))
    rate = (44100, 48000, 32000)[(c >> 2) & 3] // (1 if version == 3 else 2 if version == 2 else 4)
    size = (144 if version == 3 else 72) * bitrates[(c >> 4) - 1] * 1000 // rate + ((c >> 1) & 1)
    return len(data) >= offset + size


def inspect_voice_audio(data: bytes, *, max_bytes: int, max_seconds: float | None = None) -> str:
    """Return the STT filename derived from bytes, never a supplied extension/MIME."""
    if len(data) > max_bytes:
        raise invalid_audio("25MB보다 커요")
    if not data:
        raise invalid_audio("녹음 파일이 비어 있어요")
    duration = None
    extension = None
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        extension = ".wav"
        try:
            with wave.open(io.BytesIO(data)) as audio:
                frames = audio.getnframes()
                frame_bytes = audio.getnchannels() * audio.getsampwidth()
                if frames == 0 or len(audio.readframes(frames)) != frames * frame_bytes:
                    raise wave.Error("empty or truncated PCM")
                duration = frames / audio.getframerate()
        except (wave.Error, EOFError, ValueError, ZeroDivisionError, struct.error):
            raise invalid_audio("녹음 파일을 읽을 수 없어요") from None
    elif _mp3_frame(data):
        extension = ".mp3"
    elif data.startswith(b"ID3"):
        # An ID3 tag alone is not audio. Locate the MPEG frame after its tag.
        if len(data) < 10 or any(byte & 0x80 for byte in data[6:10]):
            raise invalid_audio("녹음 파일을 읽을 수 없어요")
        size = sum(byte << (7 * (3 - index)) for index, byte in enumerate(data[6:10]))
        offset = 10 + size + (10 if data[5] & 0x10 else 0)
        if not _mp3_frame(data, offset):
            raise invalid_audio("녹음 파일을 읽을 수 없어요")
        extension = ".mp3"
    elif len(data) >= 12 and data[4:8] == b"ftyp":
        # ISO-BMFF also carries video: require an audio handler, not just .m4a.
        offset = data.find(b"hdlr")
        while offset >= 0:
            if data[offset + 12:offset + 16] == b"soun":
                extension = ".m4a"
                break
            offset = data.find(b"hdlr", offset + 4)
        if extension is None and data[8:12] in (b"M4A ", b"M4B "):
            raise invalid_audio("녹음 파일을 읽을 수 없어요")
    elif data.startswith(b"\x1aE\xdf\xa3") and b"webm" in data[:4096]:
        # A generic EBML/video signature is not enough: require an audio codec.
        if b"A_OPUS" in data or b"A_VORBIS" in data:
            extension = ".webm"
    elif (data.startswith((b"fLaC", b"#!AMR"))
          or (data.startswith(b"FORM") and data[8:12] in (b"AIFF", b"AIFC"))
          or (data.startswith(b"OggS") and any(codec in data for codec in (b"OpusHead", b"\x01vorbis", b"fLaC")))
          or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0)):
        raise invalid_audio("지원하지 않는 녹음 파일 형식이에요")
    if extension is None:
        raise VoiceInputRejected("ve1-not-audio")
    if max_seconds is not None:
        if (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
                or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise invalid_audio("녹음 길이 제한을 확인할 수 없어요")
        if duration is None:
            # Do not guess compressed-media duration from byte size or silently
            # claim a configured limit was checked without a decoder/probe.
            raise invalid_audio("녹음 길이를 확인할 수 없어요")
        if duration > max_seconds:
            limit = f"{max_seconds / 60:g}분" if max_seconds % 60 == 0 else f"{max_seconds:g}초"
            raise invalid_audio(f"{limit}보다 길어요")
    return f"voice{extension}"
