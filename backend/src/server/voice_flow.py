"""통화 녹음 분석: 다운로드 -> 형식 검사 -> STT -> 통화 분석 -> 카드.

문자·링크 분석과 분리된 경로다. 다운로드 주소는 `media.url` 에서만 온다. 녹음 원본과 대본은
저장하지 않는다. 녹음은 메모리로만 받고, Job 에는 분석 결과(`VoiceCaseResult`)만 남는다.
"""

import asyncio
import time
from collections.abc import Awaitable
from typing import TypeVar

from ai.case import analyze_call, transcribe_call
from backend.src.server.budgets import CASE_ANALYZE_BUDGET_S
from backend.src.server.http_client import get_http_client
from backend.src.server.jobs import ANALYSIS_JOBS
from backend.src.server.log import get_logger
from backend.src.server.media_guard import (
    MediaDownloadBlocked,
    assert_resolves_to_public_ip,
    is_allowed_media_url,
    media_host,
)
from backend.src.server.voice_input import VoiceInputRejected, inspect_voice_audio, invalid_audio
from services.kakao_card_renderer import render_card
from services.result_card_renderer import render_voice_result_card

_log = get_logger("server")
_T = TypeVar("_T")

MAX_VOICE_FILE_BYTES = 25 * 1024 * 1024  # 25MB (whisper 한도)
VOICE_DOWNLOAD_CHUNK_BYTES = 1024 * 1024  # 1MB
VOICE_DOWNLOAD_TIMEOUT_SECONDS = 15.0
# #115 Q-7 is not decided. No arbitrary recording-length policy is enabled.
# A configured limit is checked only against measured duration (currently WAV).
MAX_VOICE_DURATION_SECONDS: float | None = None
# analyze_call() 에 줄 예산의 출발값(budgets.py). 여기서 이미 쓴 다운로드·STT 시간을 뺀다.
VOICE_ANALYZE_BUDGET_S = CASE_ANALYZE_BUDGET_S
# 이보다 적게 남으면 analyze_call()의 읽기·검색(LLM 호출)을 사실상 건너뛴다.
VOICE_MIN_ANALYZE_BUDGET_S = 5.0


async def download_voice_media(url: str) -> bytes:
    """카카오 음성 CDN 에서 녹음을 받아 바이트로 돌려준다. 디스크에 쓰지 않는다.

    청크 단위로 받으며 누적 크기를 그 자리에서 검증한다. 우리 서버가
    외부 주소로 직접 요청을 보내는 것이라, `media_guard`로 호스트 허용
    목록 검사와 DNS 재검증까지 거친 뒤에만 접속한다(SSRF 방어).
    """

    host = media_host(url)

    if not is_allowed_media_url(url):
        _log.warning("[VOICE DOWNLOAD REJECTED] reason=not_allowed")
        raise MediaDownloadBlocked("허용되지 않은 다운로드 주소")

    await assert_resolves_to_public_ip(host)

    audio = bytearray()
    async with get_http_client().stream(
        "GET", url,
        timeout=VOICE_DOWNLOAD_TIMEOUT_SECONDS,
        follow_redirects=False,
    ) as response:
        if response.status_code != 200:
            raise MediaDownloadBlocked(
                f"다운로드 실패 status={response.status_code}"
            )
        # Content-Type and URL extensions are advisory only (Kakao also
        # sends nonstandard MIME). Validate actual bytes before STT.
        async for chunk in response.aiter_bytes(VOICE_DOWNLOAD_CHUNK_BYTES):
            audio += chunk
            if len(audio) > MAX_VOICE_FILE_BYTES:
                raise invalid_audio("25MB보다 커요")

    if not audio:
        raise invalid_audio("녹음 파일이 비어 있어요")

    _log.info(f"[VOICE DOWNLOAD OK] size={len(audio)}")
    return bytes(audio)


def log_voice_stage(job_id: str | None, stage: str, status: str, started: float, **summary) -> None:
    """호출부에서 고른 고정 코드·숫자만 기록한다. 원문·예외 메시지는 넘기지 않는다."""
    details = " ".join(f"{key}={value}" for key, value in summary.items())
    _log.info(
        f"[VOICE STAGE] job_id={job_id or 'none'} stage={stage} status={status} "
        f"elapsed_s={time.monotonic() - started:.3f}" + (f" {details}" if details else "")
    )


def _voice_failure_card(job_id: str | None, card: str, started: float, reason_line: str = "") -> dict:
    result = render_card(card, {"reason_line": reason_line})
    log_voice_stage(job_id, "card", "completed", started, card=card)
    return result


async def _run_stage(job_id: str | None, stage: str, work: Awaitable[_T]) -> _T:
    """STT·통화 분석 단계를 돌리고 취소·실패를 단계 로그로 남긴다.

    두 함수는 계약상 실패를 예외가 아니라 결과로 돌려준다(ai.case). 그래도 예외가 오면
    종류만 남기고 그대로 올린다. 문구에는 대본이 섞일 수 있어 남기지 않는다. 부른 쪽
    (콜백 경로의 `_analyze_for_job`, 동기 경로의 `handle_voice_message`)이 안내 응답으로 바꾼다.
    """

    started = time.monotonic()
    try:
        return await work
    except asyncio.CancelledError:
        log_voice_stage(job_id, stage, "cancelled", started)
        raise
    except Exception as error:
        log_voice_stage(job_id, stage, "failed", started, error=type(error).__name__)
        _log.warning(f"[VOICE ANALYSIS ERROR] {stage} failed: {type(error).__name__}")
        raise


async def run_voice_analysis(media_url: str, job_id: str | None = None) -> dict:
    """음성 메시지 카드 생성: 다운로드 -> STT -> 통화 분석 -> 카드.

    입력 오류와 다운로드 실패는 VE1/VE2/R4 카드로 안내한다. STT·분석에서 난 예외는 그대로
    올린다. 대본(segments)은 이 함수의 지역 변수로만 있고 저장하지 않는다.
    """

    started = time.monotonic()

    try:
        audio_bytes = await download_voice_media(media_url)
    except VoiceInputRejected as error:
        log_voice_stage(job_id, "download", "rejected", started)
        return _voice_failure_card(job_id, error.card, started, error.reason_line)
    except asyncio.CancelledError:
        log_voice_stage(job_id, "download", "cancelled", started)
        raise
    except Exception as error:  # noqa: BLE001 — 네트워크 예외 문구에는 서명된 녹음 주소가 들어 있다. 종류만 남기고 R4 로 답한다
        log_voice_stage(job_id, "download", "failed", started, error=type(error).__name__)
        _log.warning(f"[VOICE ANALYSIS ERROR] download failed: {type(error).__name__}")
        return _voice_failure_card(job_id, "r4-unavailable", started)

    log_voice_stage(job_id, "download", "completed", started, bytes=len(audio_bytes))

    try:
        filename = inspect_voice_audio(
            audio_bytes, max_bytes=MAX_VOICE_FILE_BYTES,
            max_seconds=MAX_VOICE_DURATION_SECONDS,
        )
    except VoiceInputRejected as error:
        log_voice_stage(job_id, "validation", "rejected", started)
        return _voice_failure_card(job_id, error.card, started, error.reason_line)

    stt_started = time.monotonic()
    transcript = await _run_stage(job_id, "stt", transcribe_call(audio_bytes, filename))
    log_voice_stage(
        job_id, "stt", "failed" if transcript.failure else "completed", stt_started,
        failure=transcript.failure.value if transcript.failure else "none",
        segments=len(transcript.segments), chars=sum(len(segment.text) for segment in transcript.segments),
    )

    # 남은 예산이 5초 미만이면 analyze_call()에 0초 예산을 줘 실제 분석
    # (읽기·검색 LLM 호출)을 사실상 건너뛰게 한다 — analyze_call()은 예산이
    # 바닥이면 자체적으로 즉시 시간 초과 처리해 안전한 미확인 결과를 낸다.
    remaining_budget = VOICE_ANALYZE_BUDGET_S - (time.monotonic() - started)
    analyze_budget = (
        remaining_budget if remaining_budget >= VOICE_MIN_ANALYZE_BUDGET_S else 0.0
    )

    analysis_started = time.monotonic()
    result = await _run_stage(job_id, "analysis", analyze_call(transcript, time_budget_s=analyze_budget))
    log_voice_stage(
        job_id, "analysis",
        "unavailable" if result.conclusion.value == "not_analyzable" else "completed", analysis_started,
        conclusion=result.conclusion.value, reading_status=result.reading_status,
        failures=len(result.failures), evidence=len(result.evidence), rejected=len(result.rejected),
        failure_codes=",".join(sorted({failure.value for failure in result.failures})) or "none",
        budget_s=f"{analyze_budget:.3f}",
    )

    payload = result.model_dump(mode="json")

    if job_id is not None and (job := ANALYSIS_JOBS.get(job_id)):
        job["final_payload"] = payload

    card = render_voice_result_card(payload, job_id)
    card_name = {"not_analyzable": "ve3-unrecognized", "input_required": "ve4-no-claim"}.get(
        result.conclusion.value, "v1-voice-result",
    )
    log_voice_stage(job_id, "card", "completed", started, card=card_name)
    return card
