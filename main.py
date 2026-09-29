import asyncio
import time

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from backend.src.server.urlscan_service import (
    submit_url_scan,
    wait_for_url_scan_result,
)
from backend.src.server.url_utils import split_message
from backend.src.server.templates.renderer import render_r1_lookalike
from services.official_domain_service import check_official_domain

# AI 연결
from ai.pipeline import analyze_message_part, finalize_analysis
from ai.types import FailureCode, UrlAnalysis


app = FastAPI()


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
        "callback_status": "pending"
    }

    print(
        f"[JOB CREATED] "
        f"job_id={job_id} "
        f"user={user_id}"
    )

    return job_id


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

    # 문자 내용에서 URL과 일반 메시지 분리
    links, message = split_message(utterance)

    print(f"[LINK COUNT] {len(links)}")
    print(f"[MESSAGE] {message!r}")

    # URL이 없는 경우 즉시 응답
    if not links:
        return kakao_response(
            "URL을 찾을 수 없습니다.\n"
            "http:// 또는 https://로 시작하는 URL을 보내주세요."
        )

    # 같은 사용자의 이전 분석이 아직 진행 중인 경우
    if user_id and user_id in RUNNING_USERS:
        return kakao_response(
            "이전 요청을 아직 확인하고 있어요. "
            "잠시 후 다시 시도해주세요."
        )

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
    return {
        "version": "2.0",
        "useCallback": True,
        "data": {
            "text": (
                "링크를 확인하고 있어요. "
                "분석이 완료되면 결과를 알려드릴게요."
            )
        }
    }


# ============================================================
# Main Analysis
# ============================================================

async def run_analysis(
    links: list[str],
    message: str
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
      → page=None, failure=MISSING_RESULT
      → finalize_analysis()
    
    문자 AI 분석과 urlscan은 독립적인 작업이므로
    asyncio.create_task를 이용해 병렬로 실행한다.
    """

    result_lines = []

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
        try:
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

                result_lines.append(
                    f"⚠️ 검사 요청 실패: {link}"
                )

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

                result_lines.append(
                    f"⚠️ 검사 시간 초과: {link}"
                )

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
                "message + missing environment result"
            )
            
            final_result = await finalize_analysis(
                url=url_analysis,
                message=message_result,
                page=None,
                failure=FailureCode.MISSING_RESULT
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
            # 4. 테스트용 Kakao 출력
            # ------------------------------------------------

            result_lines.append(
                format_ai_result(
                    link,
                    final_payload
                )
            )

        except Exception as e:
            print(
                f"[ANALYSIS ERROR] "
                f"url={link} "
                f"{type(e).__name__}: {e}"
            )

            result_lines.append(
                f"⚠️ 분석 실패: {link}"
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

    if not result_lines:
        return kakao_response(
            "분석 결과를 생성하지 못했습니다."
        )

    return kakao_response(
        "\n\n".join(result_lines)
    )


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
                    message
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

        async with httpx.AsyncClient() as client:
            response = await client.post(
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
        
                print(
                    f"[JOB CALLBACK FAILED] "
                    f"job_id={job_id} "
                    f"status={callback_result_status}"
                )
        
        except ValueError:
            job = ANALYSIS_JOBS.get(job_id)
        
            if job:
                job["callback_status"] = "failed"
        
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
# AI Result → 테스트용 Kakao 문자열
# ============================================================

def format_ai_result(
    original_url: str,
    ai_result
) -> dict:
    """
    AI AnalysisResponse를 카카오톡 simpleText 응답으로 변환한다.

    새 AnalysisResponse 스키마:
    - url.official: DomainMatch
    - url.scan.score / scanned_at
    - message/env.details.doubts
    - message/env.details.signals
    - message/env.details.reason.text
    - message/env.details.reason.failures
    """

    if hasattr(ai_result, "model_dump"):
        data = ai_result.model_dump(
            mode="json"
        )
    else:
        data = ai_result

    url_data = data.get("url") or {}
    scan_data = url_data.get("scan") or {}

    message_data = data.get("message") or {}
    message_details = (
        message_data.get("details") or {}
    )
    message_reason = (
        message_details.get("reason") or {}
    )

    env_data = data.get("env") or {}
    env_details = (
        env_data.get("details") or {}
    )
    env_reason = (
        env_details.get("reason") or {}
    )

    def format_doubts(
        doubts: list | None
    ) -> str:
        if not doubts:
            return "없음"

        return "\n".join(
            (
                f"- {item.get('value', '알 수 없음')}"
                f" ({item.get('evidence', '근거 없음')})"
            )
            for item in doubts
        )

    def format_signals(
        signals: list | None
    ) -> str:
        if not signals:
            return "없음"

        return "\n".join(
            (
                f"- {item.get('code', 'unknown')}"
                f" ({item.get('evidence', '근거 없음')})"
            )
            for item in signals
        )

    def format_failures(
        failures: list | None
    ) -> str:
        if not failures:
            return "없음"

        return ", ".join(
            str(failure)
            for failure in failures
        )

    message_doubts = format_doubts(
        message_details.get("doubts")
    )
    message_signals = format_signals(
        message_details.get("signals")
    )
    message_failures = format_failures(
        message_reason.get("failures")
    )

    env_doubts = format_doubts(
        env_details.get("doubts")
    )
    env_signals = format_signals(
        env_details.get("signals")
    )
    env_failures = format_failures(
        env_reason.get("failures")
    )

    text = (
        "🔎 AI 분석 완료\n\n"

        "[URL 분석]\n"
        f"입력 URL: {original_url}\n"
        f"최종 URL: "
        f"{url_data.get('final_url', '없음')}\n"
        f"도메인: "
        f"{url_data.get('domain', '없음')}\n"
        f"official: "
        f"{url_data.get('official', '없음')}\n"
        f"scan score: "
        f"{scan_data.get('score', '없음')}\n"
        f"scanned_at: "
        f"{scan_data.get('scanned_at', '없음')}\n\n"

        "[문자 분석]\n"
        f"brand: "
        f"{message_data.get('brand') or '없음'}\n"
        f"category: "
        f"{message_data.get('category') or '없음'}\n"
        f"answer: "
        f"{message_data.get('answer', '없음')}\n"
        f"doubts:\n{message_doubts}\n"
        f"signals:\n{message_signals}\n"
        f"reason: "
        f"{message_reason.get('text') or '없음'}\n"
        f"failures: {message_failures}\n\n"

        "[환경 분석]\n"
        f"brand: "
        f"{env_data.get('brand') or '없음'}\n"
        f"category: "
        f"{env_data.get('category') or '없음'}\n"
        f"answer: "
        f"{env_data.get('answer', '없음')}\n"
        f"collected_at: "
        f"{env_data.get('collected_at') or '없음'}\n"
        f"doubts:\n{env_doubts}\n"
        f"signals:\n{env_signals}\n"
        f"reason: "
        f"{env_reason.get('text') or '없음'}\n"
        f"failures: {env_failures}\n\n"

        "[최종 판정]\n"
        f"result: {data.get('result', '없음')}"
    )

    return kakao_response(text)

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
