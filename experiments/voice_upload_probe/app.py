"""통화 녹음 업로드 프로브 — 카카오 챗봇이 오디오 파일을 스킬 서버로 넘겨주는지 확인한다.

운영 봇(`main.py`)과 분리된 일회성 실험 서버다. 테스트용 봇의 폴백 블록에 이 서버의
`/skill` 을 연결하고 카톡에서 녹음 파일을 보내면 다음을 기록한다.

1. 요청 본문 전체 → `probe_logs/<시각>-payload.json` (오디오가 어느 필드로 오는지)
2. 본문 안의 모든 URL 후보와 그 위치(JSON 경로)
3. callbackUrl 이 있으면: 즉시 "받았어요" 응답 → 백그라운드에서 파일 다운로드
   (크기·Content-Type·소요 시간 측정) → 선택적 STT → 콜백으로 결과 전송
4. `PROBE_CALLBACK_DELAY` 초만큼 콜백을 늦춰 콜백 만료 시간(1분? 5분?)을 잰다.

실행: `uvicorn experiments.voice_upload_probe.app:app --port 8000` (repo 루트에서)
절차와 판단 기준: `docs/experiments/2026-10-27-voice-upload-probe.md`
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from backend.src.server.callback_guard import callback_host, is_allowed_callback_url

app = FastAPI()

LOG_DIR = Path(os.getenv("PROBE_LOG_DIR", "probe_logs"))
# 카카오 CDN 호스트가 문서에 없어서 넓게 잡는다. 로그의 [MEDIA HOST] 를 보고 좁힌다.
DEFAULT_MEDIA_SUFFIXES = ("kakaocdn.net", "kakao.com", "daumcdn.net", "kakaousercontent.com")
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
SIMPLE_TEXT_LIMIT = 1000


def _media_suffixes() -> tuple[str, ...]:
    raw = os.getenv("PROBE_MEDIA_HOSTS", "")
    hosts = tuple(h.strip().lower() for h in raw.split(",") if h.strip())
    return hosts or DEFAULT_MEDIA_SUFFIXES


def is_allowed_media_url(url: str) -> bool:
    """우리 서버가 아무 주소나 받아 오지 않도록 https + 카카오 계열 호스트만 내려받는다."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host or "@" in parts.netloc:
        return False
    return any(host == s or host.endswith("." + s) for s in _media_suffixes())


def find_urls(node: object, path: str = "$") -> list[tuple[str, str]]:
    """본문 전체를 돌며 (JSON 경로, URL) 을 모은다. 오디오가 utterance 가 아닌
    params·detailParams·plugin 필드로 올 수도 있어서 특정 필드만 보지 않는다."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "callbackUrl":
                continue
            found.extend(find_urls(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(find_urls(value, f"{path}[{i}]"))
    elif isinstance(node, str):
        if node.lstrip().startswith(("{", "[")):
            # 플러그인 값은 JSON 문자열로 한 번 더 감싸져 오기도 한다.
            try:
                return find_urls(json.loads(node), f"{path}<json>")
            except ValueError:
                pass
        found.extend((path, m.rstrip(".,)")) for m in URL_PATTERN.findall(node))
    return found


def mask_body(body: dict) -> dict:
    """callbackUrl 경로에는 1회용 토큰이 있어 저장 전에 호스트만 남긴다."""
    masked = json.loads(json.dumps(body))
    user_request = masked.get("userRequest", {})
    if user_request.get("callbackUrl"):
        user_request["callbackUrl"] = f"https://{callback_host(user_request['callbackUrl'])}/<masked>"
    return masked


def save_log(name: str, data: dict) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = LOG_DIR / f"{stamp}-{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def text_response(text: str) -> dict:
    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": text[:SIMPLE_TEXT_LIMIT]}}]},
    }


def summarize_request(body: dict, urls: list[tuple[str, str]]) -> str:
    user_request = body.get("userRequest", {})
    lines = [
        f"utterance: {user_request.get('utterance', '')[:200]!r}",
        f"블록: {user_request.get('block', {}).get('name')}",
        f"callbackUrl: {'있음' if user_request.get('callbackUrl') else '없음'}",
        f"params 키: {sorted((body.get('action', {}).get('params') or {}).keys())}",
        f"URL 후보 {len(urls)}개",
    ]
    lines += [f"- {path}: {urlsplit(url).hostname}" for path, url in urls[:5]]
    return "\n".join(lines)


async def download(url: str, dest_dir: Path) -> dict:
    """파일을 받아 저장하고 측정값을 돌려준다. 실패해도 예외 대신 결과에 담는다."""
    started = time.perf_counter()
    info: dict = {"host": urlsplit(url).hostname}
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                info["status"] = response.status_code
                info["content_type"] = response.headers.get("content-type")
                info["content_disposition"] = response.headers.get("content-disposition")
                if response.status_code != 200:
                    return info
                dest_dir.mkdir(parents=True, exist_ok=True)
                suffix = Path(urlsplit(url).path).suffix or ".bin"
                path = dest_dir / f"{datetime.now():%Y%m%d-%H%M%S}{suffix}"
                size = 0
                with path.open("wb") as f:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            info["error"] = "too_large"
                            break
                        f.write(chunk)
                info["bytes"] = size
                info["saved_to"] = str(path)
    except httpx.HTTPError as error:
        info["error"] = type(error).__name__
    info["seconds"] = round(time.perf_counter() - started, 2)
    return info


def transcribe(path: str) -> dict:
    """faster-whisper 가 설치돼 있을 때만 STT 소요 시간을 잰다. 없으면 건너뛴다."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return {"skipped": "faster-whisper 미설치"}
    started = time.perf_counter()
    model = WhisperModel(os.getenv("PROBE_WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    segments, meta = model.transcribe(path, language="ko")
    text = " ".join(s.text.strip() for s in segments)
    return {
        "audio_seconds": round(meta.duration, 1),
        "stt_seconds": round(time.perf_counter() - started, 1),
        "text": text,
    }


async def probe_and_callback(urls: list[str], callback_url: str, received_at: float) -> None:
    report: dict = {"downloads": []}
    for url in urls:
        if not is_allowed_media_url(url):
            report["downloads"].append({"host": urlsplit(url).hostname, "skipped": "허용 목록 밖"})
            continue
        info = await download(url, LOG_DIR / "media")
        if info.get("saved_to") and not info.get("error"):
            info["stt"] = await asyncio.to_thread(transcribe, info["saved_to"])
        report["downloads"].append(info)

    delay = float(os.getenv("PROBE_CALLBACK_DELAY", "0"))
    if delay:
        await asyncio.sleep(delay)

    report["elapsed_before_callback"] = round(time.perf_counter() - received_at, 1)
    lines = [f"콜백까지 {report['elapsed_before_callback']}초"]
    for d in report["downloads"]:
        lines.append(json.dumps({k: v for k, v in d.items() if k != "stt"}, ensure_ascii=False))
        stt = d.get("stt") or {}
        if stt.get("text"):
            lines.append(f"STT {stt['stt_seconds']}초 / 음성 {stt['audio_seconds']}초")
            lines.append(stt["text"][:300])
        elif stt:
            lines.append(f"STT: {stt}")
    if not report["downloads"]:
        lines.append("URL 후보 없음")

    try:
        if not is_allowed_callback_url(callback_url):
            raise ValueError("허용되지 않은 callbackUrl")
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            response = await client.post(callback_url, json=text_response("\n".join(lines)))
        report["callback_status"] = response.status_code
        try:
            report["callback_result"] = response.json()
        except ValueError:
            report["callback_result"] = None
    except Exception as error:  # 토큰이 담긴 URL 이 메시지에 섞이지 않게 유형만 남긴다.
        report["callback_error"] = type(error).__name__

    print(f"[PROBE CALLBACK] {json.dumps({k: v for k, v in report.items() if k != 'downloads'})}")
    save_log("report", report)


@app.post("/skill")
async def skill(request: Request, background_tasks: BackgroundTasks):
    received_at = time.perf_counter()
    body = await request.json()
    log_path = save_log("payload", mask_body(body))
    urls = find_urls(body)
    callback_url = body.get("userRequest", {}).get("callbackUrl")

    print(f"[PROBE] saved={log_path} urls={len(urls)} callback={bool(callback_url)}")
    for path, url in urls:
        print(f"[MEDIA HOST] {path} {urlsplit(url).hostname}")

    summary = summarize_request(body, urls)
    if not callback_url:
        return text_response(f"[콜백 없음 — 받은 값만 보여줘요]\n{summary}")

    # 같은 파일 주소가 utterance 와 params.media.url 에 함께 와서 한 번만 받는다.
    unique_urls = list(dict.fromkeys(url for _, url in urls))
    background_tasks.add_task(probe_and_callback, unique_urls, callback_url, received_at)
    return {
        "version": "2.0",
        "useCallback": True,
        "data": {"text": f"받았어요. 내려받는 중이에요.\n{summary}"[:SIMPLE_TEXT_LIMIT]},
    }
