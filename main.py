import asyncio
import os
import time

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from backend.src.server.shadow_tasks import track_shadow_task
from backend.src.server.urlscan_service import (
    get_http_client,
    submit_url_scan,
    wait_for_url_scan_result,
)
from backend.src.server.lifespan import app_lifespan
from backend.src.server.url_utils import split_message
from backend.src.server.templates.renderer import render_r1_lookalike
from services.official_domain_service import check_official_domain
from services.kakao_card_renderer import render_card
from services.result_card_renderer import render_result_card, merge_kakao_responses

# AI 연결
from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import UrlAnalysis

from scanner.isolation_client import collect_url_isolated
from scanner.static_checks import check_static
from scanner.models import UrlEvidence
from urllib.parse import urlparse


app = FastAPI(lifespan=app_lifespan)

# Collector Shadow Mode는 기본적으로 비활성화한다.
# 테스트 환경에서만 명시적으로 활성화한다.
ENABLE_URL_COLLECTOR_SHADOW = (
    os.getenv("ENABLE_URL_COLLECTOR_SHADOW", "false").lower() == "true"
    and os.getenv("APP_ENV", "production").lower() in {"local", "test", "staging"}
)

async def log_collector_shadow(
    collector_task: asyncio.Task,
    link: str,
    parsed_result: dict,
    urlscan_elapsed: float,
):
    try:
        collector_result = await collector_task

        collector_domain = None

        if collector_result.final_url:
            collector_domain = urlparse(
                collector_result.final_url
            ).hostname

        print("========== URL COLLECTOR SHADOW ==========")
        print(f"[INPUT URL]             {collector_result.input_url}")
        print(f"[URLSCAN FINAL URL]     {parsed_result.get('final_url')}")
        print(f"[COLLECTOR FINAL URL]   {collector_result.final_url}")
        print(
            f"[FINAL URL MATCH]       "
            f"{parsed_result.get('final_url') == collector_result.final_url}"
        )
        print(f"[URLSCAN DOMAIN]        {parsed_result.get('domain')}")
        print(f"[COLLECTOR DOMAIN]      {collector_domain}")
        print(
            f"[DOMAIN MATCH]          "
            f"{parsed_result.get('domain') == collector_domain}"
        )
        print(f"[URLSCAN ELAPSED]       {urlscan_elapsed:.2f}s")
        print(f"[COLLECTOR ELAPSED]     {collector_result.elapsed_ms}ms")
        print(f"[COLLECTOR STATUS]      {collector_result.status_code}")
        print(f"[COLLECTOR TITLE]       {collector_result.title}")
        print(f"[COLLECTOR REDIRECTS]   {collector_result.redirect_chain}")
        print(f"[COLLECTOR FAILURES]    {collector_result.failures}")
        print("==========================================")

    except asyncio.CancelledError:
        print(f"[COLLECTOR SHADOW CANCELLED] url={link}")
        raise

    except Exception as e:
        print(
            f"[COLLECTOR SHADOW ERROR] "
            f"url={link} "
            f"{type(e).__name__}: {e}"
        )


# TODO:
# 현재는 콜백 기능 테스트를 위한 임시 인메모리 저장소.
# 추후 PostgreSQL 또는 Redis 기반 작업 상태 관리로 변경.
RUNNING_USERS: set[str] = set()


# TODO:
# 프로토타입 검증용 인메모리 작업 저장소.
# 서버 재시작 시 데이터가 소실된다.
# 추후 PostgreSQL 기반 저장소로 교체한다.
ANALYSIS_JOBS: dict[str, dict] = {}


# 콜백 URL 유효 시간을 고려한 안전 마진
CALLBACK_DEADLINE_SECONDS = 45.0


# ============================================================
# Store Analysis Job
# ============================================================

def create_analysis_job(
    user_id: str | None
) -> str:
    """
    새로운 분석 작업을 생성하고 job_id를 반환한다.

    현재는 인메모리 저장소를 사용하며,
    추후 PostgreSQL 기반 저장소로 교체한다.
    """

    job_id = str(uuid.uuid4())

    ANALYSIS_JOBS[job_id] = {
        "job_id": job_id,
        "user_id": user_id,
        "status": "running",
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
        "callback_status": "pending",
        "callback_error": None
    }

    print(
        f"[JOB CREATED] "
        f"job_id={job_id} "
        f"user={user_id}"
    )

    return job_id


# ============================================================
# Find Latest Job
# ============================================================

def find_latest_job_by_user(
    user_id: str | None
) -> dict | None:
    if not user_id:
        return None

    user_jobs = [
        job
        for job in ANALYSIS_JOBS.values()
        if job.get("user_id") == user_id
    ]

    if not user_jobs:
        return None

    return max(
        user_jobs,
        key=lambda job: job["created_at"]
    )

# ============================================================
# Analysis Job API
# ============================================================

@app.get("/api/analyses/{job_id}")
async def get_analysis_job(job_id: str):
    """
    인메모리에 저장된 분석 작업 상태와 결과를 조회한다.

    현재는 개발/테스트용 API이며,
    서버 재시작 시 저장된 작업은 소실된다.
    """

    job = ANALYSIS_JOBS.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Analysis job not found"
        )

    return {
        "success": True,
        "job": job
    }


# ============================================================
# Time Format
# ============================================================

def format_completed_at_kst(
    completed_at: str | None
) -> str | None:
    """UTC completed_at을 한국 시간(KST, UTC+9) HH:MM으로 변환한다."""

    if not completed_at:
        return None

    try:
        completed_datetime = datetime.fromisoformat(
            completed_at.replace("Z", "+00:00")
        )

        if completed_datetime.tzinfo is None:
            completed_datetime = completed_datetime.replace(
                tzinfo=timezone.utc
            )

        kst = timezone(
            timedelta(hours=9)
        )

        kst_datetime = completed_datetime.astimezone(
            kst
        )

        return kst_datetime.strftime("%H:%M")

    except (ValueError, TypeError):
        return None


# ============================================================
# Handle Check Result
# ============================================================

def handle_check_result(
    user_id: str | None
) -> dict:
    job = find_latest_job_by_user(user_id)

    if job is None:
        return render_card("k4-no-result")

    status = job.get("status")

    if status == "running":
        return render_card("k1-still-running")

    if status == "completed":
        result = job.get("result")

        if not result:
            return render_card("k4-no-result")

        completed_time = format_completed_at_kst(
            job.get("completed_at")
        )

        if not completed_time:
            return result

        time_message = kakao_response(
            f"{completed_time}에 확인한 결과예요."
        )

        return merge_kakao_responses([
            time_message,
            result,
        ])

    if status in ("failed", "timeout"):
        return render_card("r4-unavailable")

    return render_card("k4-no-result")


# ============================================================
# Kakao Skill
# ============================================================

@app.post("/kakao/skill")
async def kakao_skill(
    request: Request,
    background_tasks: BackgroundTasks
):
    body = await request.json()

    user_request = body.get("userRequest", {})

    utterance = user_request.get("utterance", "")
    user_id = user_request.get("user", {}).get("id")
    callback_url = user_request.get("callbackUrl")

    print(
        f"[KAKAO] "
        f"user={user_id} "
        f"utterance={utterance}"
    )

    print(
        f"[CALLBACK EXISTS] "
        f"{bool(callback_url)}"
    )
    
    intent_name = (
        body.get("intent", {}).get("name", "")
    )
    
    # 사용자가 결과 확인 요청하면 최근 분석 결과를 리턴
    if intent_name == "결과 확인":
        return handle_check_result(user_id)

    # 문자 내용에서 URL과 일반 메시지 분리
    links, message = split_message(utterance)

    print(f"[LINK COUNT] {len(links)}")
    print(f"[MESSAGE] {message!r}")

    # URL이 없는 경우 즉시 응답
    if not links:
        return render_card("r6-input-required")

    # 같은 사용자의 이전 분석이 아직 진행 중인 경우
    if user_id and user_id in RUNNING_USERS:
        return render_card("k5-duplicate")

    # callbackUrl이 없는 경우 개발/테스트용 동기 처리
    if not callback_url:
        print("[CALLBACK] callbackUrl 없음 - 동기 처리")

        return await run_analysis(
            links,
            message
        )

    # 사용자 분석 시작 상태 저장
    if user_id:
        RUNNING_USERS.add(user_id)

    # 분석 작업 생성
    job_id = create_analysis_job(
        user_id
    )

    # 오래 걸리는 분석은 background task에서 수행
    background_tasks.add_task(
        run_analysis_and_callback,
        links,
        message,
        callback_url,
        user_id,
        job_id
    )

    # 카카오에는 즉시 callback 사용 응답
    return render_card("r5-analyzing")


# ============================================================
# Main Analysis
# ============================================================

async def run_analysis(
    links: list[str],
    message: str,
    job_id: str | None = None
) -> dict:
    """
    현재 AI-BE 연결 테스트 흐름

    message
      → AI 문자 분석

    URL
      → urlscan
      → score 추출
      → 문자 brand + 최종 domain 기반 official 계산
      → UrlAnalysis 생성

    최종 분석
      → message 결과 전달
      → 격리 페이지 수집기는 아직 미연결
      → page=None으로 전달하여 not_run 처리
      → finalize_analysis()

    문자 AI 분석과 urlscan은 독립적인 작업이므로
    asyncio.create_task를 이용해 병렬로 실행한다.

    job_id는 결과 카드의 "자세히 보기" 버튼에 꽂혀서,
    해당 버튼 클릭 시 어떤 분석 결과를 보여줄지 찾는 데 쓰인다
    (Job 조회 자체는 이 함수의 책임이 아니다).
    """

    card_responses: list[dict] = []

    total_start = time.monotonic()

    # ========================================================
    # 1. 문자 AI 분석 — urlscan과 독립적이므로 "시작만" 해두고
    #    결과가 실제로 필요한 시점(2-4 이후)에 가서 기다린다.
    # ========================================================

    print("========== AI MESSAGE ANALYSIS (병렬 시작) ==========")
    print(f"[AI MESSAGE INPUT] {message!r}")

    message_start = time.monotonic()
    message_task = asyncio.create_task(
        analyze_message_part(message)
    )
    message_logged = False

    # ========================================================
    # 2. URL별 분석 (문자 분석과 동시에 진행)
    # ========================================================

    for link in links:
        collector_task = None
        collector_result = None
        try:
            input_domain = urlparse(link).hostname

            input_static = check_static(
                domain=input_domain or "",
                brand=None,
            )

            print(
                "[STATIC INPUT]",
                {
                    "domain": input_static.domain,
                    "official_match": input_static.official_match,
                    "is_punycode": input_static.is_punycode,
                    "decoded_domain": input_static.decoded_domain,
                    "kisa_listed": input_static.kisa_listed,
                    "lookalike_of": input_static.lookalike_of,
                    "failures": input_static.failures,
                }
            )
            
            # 자체 URL Collector를 urlscan과 병렬 실행한다.
            # 현재는 Shadow Mode이므로 실제 분석 결과에는 사용하지 않는다.

            if ENABLE_URL_COLLECTOR_SHADOW:
                collector_task = asyncio.create_task(
                    collect_url_isolated(link)
                )
                track_shadow_task(collector_task)
            urlscan_start = time.monotonic()

            # ------------------------------------------------
            # 2-1. urlscan 요청
            # ------------------------------------------------

            submit_result = await submit_url_scan(
                link
            )

            scan_id = submit_result.get("uuid")

            if not scan_id:
                print(
                    f"[URLSCAN ERROR] "
                    f"url={link} uuid 없음"
                )

                card_responses.append(
                    render_card("r4-unavailable")
                )

                if collector_task is not None:
                    collector_task.cancel()

                continue

            print(
                f"[URLSCAN] "
                f"url={link} "
                f"scan_id={scan_id}"
            )

            # ------------------------------------------------
            # 2-2. urlscan 결과 대기
            # ------------------------------------------------

            scan_result = (
                await wait_for_url_scan_result(
                    scan_id
                )
            )

            if scan_result is None:
                print(
                    f"[URLSCAN TIMEOUT] "
                    f"url={link}"
                )

                card_responses.append(
                    render_card("r4-unavailable")
                )
                if collector_task is not None:
                    collector_task.cancel()

                continue

            urlscan_elapsed = (
                time.monotonic() - urlscan_start
            )

            print(
                f"[TIMING] urlscan 소요: "
                f"{urlscan_elapsed:.2f}s "
                f"({link})"
            )

            # ------------------------------------------------
            # 2-3. 결과 파싱
            # ------------------------------------------------

            parsed_result = parse_urlscan_result(
                scan_result
            )

            print(
                f"[PARSED RESULT] "
                f"{parsed_result}"
            )
            # ------------------------------------------------
            # 2-3-1. 자체 Collector Shadow 비교
            # ------------------------------------------------

            if collector_task is not None:
                shadow_log_task = asyncio.create_task(
                    log_collector_shadow(
                        collector_task,
                        link,
                        parsed_result,
                        urlscan_elapsed,
                    )
                )
                # Shadow 로그 작업을 백그라운드 작업 목록에 등록한다.
                track_shadow_task(shadow_log_task)
                # Collector의 소유권을 Shadow 로그 작업으로 넘긴다.
                # 이후 AI 분석에서 예외가 발생해도 Collector를 취소하지 않는다.
                collector_task = None

            # ------------------------------------------------
            # 2-4. print urlscan result
            # ------------------------------------------------

            print(
                "========== URLSCAN RESULT =========="
            )
            print(
                f"[INPUT URL]   "
                f"{parsed_result.get('url')}"
            )
            print(
                f"[FINAL URL]   "
                f"{parsed_result.get('final_url')}"
            )
            print(
                f"[DOMAIN]      "
                f"{parsed_result.get('domain')}"
            )
            print(
                f"[SCORE]       "
                f"{parsed_result.get('score')}"
            )
            print(
                f"[MALICIOUS]   "
                f"{parsed_result.get('malicious')}"
            )
            print(
                f"[CATEGORIES]  "
                f"{parsed_result.get('categories')}"
            )
            print(
                f"[BRANDS]      "
                f"{parsed_result.get('brands')}"
            )
            print(
                "===================================="
            )

            # ------------------------------------------------
            # 2-5. 병렬로 시작해둔 문자 분석 결과 대기
            #
            # urlscan이 문자 분석보다 훨씬 오래 걸리므로, 여기 도착할
            # 때는 이미 message_task가 끝나 있을 가능성이 높다 —
            # 이 await는 대부분 즉시 반환된다 (한 번 끝난 task는
            # 몇 번을 다시 await해도 캐시된 결과를 즉시 돌려준다).
            # ------------------------------------------------

            message_wait_start = time.monotonic()

            try:
                message_result = await message_task

                if not message_logged:
                    message_elapsed = (
                        time.monotonic() - message_start
                    )

                    print(
                        f"[TIMING] 문자 분석 총 소요: "
                        f"{message_elapsed:.2f}s "
                        f"(urlscan과 겹친 시간 포함, "
                        f"이 지점에서 실제로 기다린 시간: "
                        f"{time.monotonic() - message_wait_start:.2f}s)"
                    )

                    print(
                        "[AI MESSAGE RESULT]",
                        message_result.model_dump(
                            mode="json"
                        )
                    )

                    message_logged = True

            except Exception as e:
                if not message_logged:
                    print(
                        f"[AI MESSAGE ERROR] "
                        f"{type(e).__name__}: {e}"
                    )

                    message_logged = True

                # finalize_analysis는 message=None도 처리 가능
                message_result = None

            # ============================================================
            # BE 공식 도메인 대조
            # ============================================================
            
            brand = (
                message_result.brand
                if message_result is not None
                else None
            )
            # ============================================================
            # BE URL scanner evidence 조합 (Shadow Mode)
            # ============================================================

            # 문자 분석이 끝났으므로 brand를 반영해 입력 도메인을 다시 검사한다.
            input_static = check_static(
                domain=input_domain or "",
                brand=brand,
            )

            final_static = None

            # Collector가 확인한 최종 URL이 있으면 redirect 이후 도메인도 검사한다.
            if collector_result is not None:
                final_domain = None

                if collector_result.final_url:
                    final_domain = urlparse(
                        collector_result.final_url
                    ).hostname

                if final_domain:
                    final_static = check_static(
                        domain=final_domain,
                        brand=brand,
                    )

                url_evidence = UrlEvidence(
                    input_static=input_static,
                    final_static=final_static,
                    collector=collector_result,
                    urlscan=parsed_result,
                )

                print(
                    "[URL EVIDENCE]",
                    {
                        "input_domain": url_evidence.input_static.domain,
                        "input_official": url_evidence.input_static.official_match,
                        "input_lookalike": url_evidence.input_static.lookalike_of,
                        "final_domain": (
                            url_evidence.final_static.domain
                            if url_evidence.final_static
                            else None
                        ),
                        "final_official": (
                            url_evidence.final_static.official_match
                            if url_evidence.final_static
                            else None
                        ),
                        "final_lookalike": (
                            url_evidence.final_static.lookalike_of
                            if url_evidence.final_static
                            else None
                        ),
                        "collector_failures": url_evidence.collector.failures,
                        "urlscan_score": url_evidence.urlscan.get("score"),
                        "urlscan_malicious": url_evidence.urlscan.get("malicious"),
                    }
                )
            
            url_analysis = build_ai_url_analysis(
                parsed_result,
                brand
            )
            
            print(
                "[DOMAIN MATCH]",
                {
                    "brand": brand,
                    "domain": url_analysis.domain,
                    "official": url_analysis.official
                }
            )

            # =================================================
            # 3. AI 최종 분석
            # =================================================

            print(
                "========== AI FINAL ANALYSIS =========="
            )

            print(
                "[AI FINAL] "
                f"domain_match={url_analysis.official} → "
                "message + environment not_run"
            )
            
            final_result = await finalize_analysis(
                url=url_analysis,
                message=message_result,
                page=None
            )

            final_payload = final_result.model_dump(
                mode="json"
            )

            print(
                "[AI FINAL RESULT]",
                final_payload
            )

            print(
                "======================================="
            )

            # ------------------------------------------------
            # 4. Kakao 결과 카드
            # ------------------------------------------------

            card_responses.append(
                render_result_card(
                    final_payload,
                    job_id=job_id
                )
            )

        except Exception as e:
            if (
                collector_task is not None
                and not collector_task.done()
            ):
                collector_task.cancel()
            print(
                f"[ANALYSIS ERROR] "
                f"url={link} "
                f"{type(e).__name__}: {e}"
            )

            card_responses.append(
                render_card("r4-unavailable")
            )

    # 모든 링크가 위에서 continue로 건너뛰어졌다면 message_task를
    # 한 번도 await하지 않았을 수 있다 — 여기서 정리해서 background에
    # 방치된 채로 남지 않게 한다 (asyncio가 아직 완료 안 된 task를
    # 아무도 안 기다리면 경고를 남긴다).
    if not message_task.done():
        message_task.cancel()

    try:
        await message_task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        if not message_logged:
            print(
                f"[AI MESSAGE ERROR] "
                f"{type(e).__name__}: {e}"
            )

    # ========================================================
    # 5. Kakao 응답
    # ========================================================

    total_elapsed = time.monotonic() - total_start

    print(
        f"[TIMING] run_analysis 전체 소요: "
        f"{total_elapsed:.2f}s"
    )

    return merge_kakao_responses(card_responses)


# ============================================================
# URLSCAN → AI Adapter
# ============================================================

def build_ai_url_analysis(
    parsed_result: dict,
    brand: str | None
) -> UrlAnalysis:
    """
    URL 분석 결과를 AI UrlAnalysis 계약으로 변환한다.

    official은 urlscan score로 추론하지 않고,
    문자 분석에서 확인한 brand와 최종 domain을
    BE 화이트리스트로 대조하여 결정한다.

    urlscan score는 판정과 분리된 기록용 값이다.
    """

    final_url = parsed_result.get("final_url")
    domain = parsed_result.get("domain")
    score = parsed_result.get("score")

    if not final_url:
        raise ValueError(
            "urlscan 결과에 final_url이 없습니다."
        )

    if not domain:
        raise ValueError(
            "urlscan 결과에 domain이 없습니다."
        )

    official = check_official_domain(
        brand=brand,
        domain=domain
    )

    raw_url_result = {
        "final_url": final_url,
        "domain": domain,
        "official": official,
        "scan": {
            "score": score,
            "scanned_at": datetime.now(
                timezone.utc
            ).isoformat()
        }
    }

    print(
        "[AI URL INPUT]",
        raw_url_result
    )

    return UrlAnalysis.model_validate(
        raw_url_result
    )


# ============================================================
# Kakao callback
# ============================================================

async def run_analysis_and_callback(
    links: list[str],
    message: str,
    callback_url: str,
    user_id: str | None,
    job_id: str
):
    """
    백그라운드에서 분석을 수행한 뒤
    카카오 callbackUrl로 최종 결과를 전송한다.
    """

    try:
        # ----------------------------------------------------
        # 1. 분석 수행
        # ----------------------------------------------------

        try:
            result = await asyncio.wait_for(
                run_analysis(
                    links,
                    message,
                    job_id=job_id
                ),
                timeout=CALLBACK_DEADLINE_SECONDS
            )

            # callback 전송 전에 분석 결과 저장
            job = ANALYSIS_JOBS.get(job_id)
        
            if job:
                job["status"] = "completed"
                job["result"] = result
                job["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
        
                print(
                    f"[JOB COMPLETED] "
                    f"job_id={job_id}"
                )

        except asyncio.TimeoutError:
            print(
                f"[ANALYSIS TIMEOUT] "
                f"user={user_id} "
                f"links={links}"
            )

            result = kakao_response(
                "분석이 예상보다 오래 걸리고 있어요. "
                "잠시 후 다시 확인해주세요."
            )

            job = ANALYSIS_JOBS.get(job_id)

            if job:
                job["status"] = "timeout"
                job["result"] = result
                job["error"] = "analysis_timeout"
                job["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()

            print(
                f"[JOB TIMEOUT] "
                f"job_id={job_id}"
            )

        except Exception as e:
            print(
                f"[ANALYSIS ERROR] "
                f"{type(e).__name__}: {e}"
            )

            result = kakao_response(
                "분석 중 문제가 발생했습니다. "
                "잠시 후 다시 시도해주세요."
            )

            job = ANALYSIS_JOBS.get(job_id)

            if job:
                job["status"] = "failed"
                job["result"] = result
                job["error"] = (
                    f"{type(e).__name__}: {e}"
                )
                job["completed_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
        
                print(
                    f"[JOB FAILED] "
                    f"job_id={job_id}"
                )

        # ----------------------------------------------------
        # 2. 카카오 callback 전송
        # ----------------------------------------------------

        print(
            "[CALLBACK] 결과 전송 시작"
        )

        response = await get_http_client().post(
            callback_url,
            json=result,
            timeout=10.0
        )

        print(
            f"[CALLBACK STATUS] "
            f"{response.status_code}"
        )

        print(
            f"[CALLBACK RESPONSE] "
            f"{response.text}"
        )

        response.raise_for_status()

        # ----------------------------------------------------
        # 3. Kakao callback 결과 확인
        # ----------------------------------------------------

        try:
            callback_result = response.json()
        
            callback_result_status = callback_result.get(
                "status"
            )
        
            print(
                f"[CALLBACK RESULT STATUS] "
                f"{callback_result_status}"
            )
        
            job = ANALYSIS_JOBS.get(job_id)
        
            if callback_result_status == "SUCCESS":
                if job:
                    job["callback_status"] = "success"
        
                print(
                    f"[JOB CALLBACK SUCCESS] "
                    f"job_id={job_id}"
                )
        
            else:
                if job:
                    job["callback_status"] = "failed"
                    job["callback_error"] = (
                        f"kakao_callback_status={callback_result_status}"
                    )
        
                print(
                    f"[JOB CALLBACK FAILED] "
                    f"job_id={job_id} "
                    f"status={callback_result_status}"
                )
        
        except ValueError:
            job = ANALYSIS_JOBS.get(job_id)
        
            if job:
                job["callback_status"] = "failed"
                job["callback_error"] = "invalid_callback_response"
        
            print(
                "[CALLBACK WARNING] "
                "응답을 JSON으로 파싱할 수 없습니다."
            )
        
            print(
                f"[JOB CALLBACK FAILED] "
                f"job_id={job_id}"
            )

    except Exception as e:
        job = ANALYSIS_JOBS.get(job_id)
    
        if job:
            job["callback_status"] = "failed"
            job["callback_error"] = (
                f"{type(e).__name__}: {e}"
            )
    
        print(
            f"[CALLBACK SEND FAILED] "
            f"{type(e).__name__}: {e}"
        )
    
        print(
            f"[JOB CALLBACK FAILED] "
            f"job_id={job_id}"
        )

    finally:
        if user_id:
            RUNNING_USERS.discard(
                user_id
            )

            print(
                f"[RUNNING USER REMOVED] "
                f"user={user_id}"
            )


# ============================================================
# Kakao Response
# ============================================================

def kakao_response(
    text: str
) -> dict:
    """
    카카오 SkillResponse simpleText 생성.
    """

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": text
                    }
                }
            ]
        }
    }


# ============================================================
# URLSCAN Result Parser
# ============================================================

def parse_urlscan_result(
    result: dict
) -> dict:

    task = result.get(
        "task",
        {}
    )

    page = result.get(
        "page",
        {}
    )

    verdicts = result.get(
        "verdicts",
        {}
    )

    urlscan = verdicts.get(
        "urlscan",
        {}
    )

    engines = verdicts.get(
        "engines",
        {}
    )

    overall = verdicts.get(
        "overall",
        {}
    )

    print(
        "[URLSCAN VERDICTS]",
        verdicts
    )

    return {
        # 기본 URL 정보
        "url": task.get("url"),
        "title": page.get("title"),
        "final_url": page.get("url"),
        "domain": page.get("domain"),

        # urlscan 자체 verdict
        "score": urlscan.get("score"),
        "malicious": urlscan.get(
            "malicious"
        ),
        "categories": urlscan.get(
            "categories",
            []
        ),
        "brands": urlscan.get(
            "brands",
            []
        ),

        # 비교/디버깅용 ML 정보
        "ml_score": engines.get(
            "score"
        ),
        "ml_malicious": engines.get(
            "malicious"
        ),

        # 비교/디버깅용 overall 정보
        "overall_score": overall.get(
            "score"
        ),
        "overall_malicious": overall.get(
            "malicious"
        )
    }


# ============================================================
# FE-BE R1 Card Test
# ============================================================

@app.post("/test/kakao/r1-lookalike")
async def test_r1_lookalike():
    """
    FE-BE Kakao 카드 연동 테스트용.

    실제 분석 로직을 거치지 않고
    R1 lookalike 카드를 반환한다.
    """

    return render_r1_lookalike()
