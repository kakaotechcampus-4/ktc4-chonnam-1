import httpx
from fastapi import BackgroundTasks, FastAPI, Request

from server.urlscan_service import submit_url_scan, wait_for_url_scan_result
from server.url_utils import split_message


app = FastAPI()

# TODO: 지금은 콜백 전환 자체를 검증하기 위한 임시 인메모리 저장소다.
# 실제로는 models/README.md의 chatbot_sessions/jobs 테이블로 옮겨야 하고,
# 콜백 실패·초과 시에도 사용자가 재요청으로 결과를 받을 수 있도록
# GET /api/analyses/{job_id}(api/README.md)에 해당하는 조회 경로가 필요하다.
RUNNING_USERS: set[str] = set()


@app.post("/kakao/skill")
async def kakao_skill(request: Request, background_tasks: BackgroundTasks):

    body = await request.json()

    user_request = body.get("userRequest", {})
    utterance = user_request.get("utterance", "")
    user_id = user_request.get("user", {}).get("id")
    callback_url = user_request.get("callbackUrl")

    print(f"[KAKAO] user={user_id} utterance={utterance}")

    links, message = split_message(utterance)

    print(f"[UTTERANCE LENGTH] {len(utterance)}")
    print(f"[UTTERANCE STARTS BRACKET] {utterance.startswith('[')}")

    for link in links:
        print(f"[LINK LENGTH] {len(link)}")
        print(f"[LINK STARTS BRACKET] {link.startswith('[')}")

    print(f"[KAKAO RAW] {utterance!r}")
    print(f"[LINKS RAW] {links!r}")

    if not links:
        return kakao_response(
            "URL을 찾을 수 없습니다.\n"
            "http:// 또는 https://로 시작하는 URL을 보내주세요."
        )

    # 같은 사용자의 이전 분석이 아직 끝나지 않았으면 새 작업을 만들지 않는다.
    # (orchestration/README.md: "세션이 analyzing 상태인 동안 들어오는 새 요청은
    #  새 작업을 만들지 않는다"를 세션 테이블이 생기기 전까지 흉내낸 것)
    if user_id in RUNNING_USERS:
        return kakao_response(
            "이전 요청을 아직 확인하고 있어요. 잠시 후 다시 시도해주세요."
        )

    # 카카오 콜백을 지원하지 않는 호출(callbackUrl이 없는 테스트 요청 등)이면
    # 예전처럼 동기로 끝까지 처리한다. urlscan 대기가 길면 이 경로는 5초
    # 타임아웃을 넘길 수 있다 — 실제 카카오 채널에서는 항상 callbackUrl이 온다.
    if not callback_url:
        return await run_analysis(links, message)

    # 4초 안에 끝내지 못하므로, 먼저 "확인 중" 응답을 보내고 실제 분석은
    # 백그라운드에서 계속한 뒤 callbackUrl로 결과를 전송한다 (카카오 콜백 최대 1분).
    if user_id:
        RUNNING_USERS.add(user_id)

    background_tasks.add_task(
        run_analysis_and_callback, links, message, callback_url, user_id
    )

    return {
        "version": "2.0",
        "useCallback": True,
        "data": {
            "text": "링크를 확인하고 있어요. 최대 1분 정도 걸릴 수 있어요."
        }
    }


async def run_analysis(links: list[str], message: str) -> dict:
    """urlscan 요청 → 대기 → 결과 조립까지 실제 분석을 수행하고 카카오 응답을 돌려준다."""

    submit_result_lines = []
    scan_results = []

    for link in links:
        try:
            # 1. urlscan에 검사 요청
            submit_result = await submit_url_scan(link)

            # 2. scan ID 획득
            scan_id = submit_result.get("uuid")

            print(f"[URLSCAN] url={link} scan_id={scan_id}")

            submit_result_lines.append(
                f"✅ 검사 요청됨: {link}"
            )

            # 3. 검사 결과 대기
            scan_result = await wait_for_url_scan_result(
                scan_id
            )

            # 4. 검사 결과 파싱
            if scan_result is not None:
                parsed_result = parse_urlscan_result(
                    scan_result
                )

                scan_results.append(parsed_result)

        except Exception as e:
            print(
                f"[URLSCAN ERROR] "
                f"url={link} error={e}"
            )

            submit_result_lines.append(
                f"⚠️ 검사 요청 실패: {link}"
            )

    summary = "\n".join(submit_result_lines)

    # TODO
    # message + links + scan_results를
    # AI에게 전달하여 분석
    #
    # ai_result = await analyze_smishing(
    #     message=message,
    #     links=links,
    #     scan_results=scan_results
    # )

    return kakao_response(
        f"URL 검사를 요청했습니다. "
        f"(총 {len(links)}건)\n\n"
        f"{summary}\n\n"
        f"분석 결과는 다음과 같습니다.\n"
        f"AI 분석 결과"
    )


async def run_analysis_and_callback(
    links: list[str],
    message: str,
    callback_url: str,
    user_id: str | None,
):
    """백그라운드에서 분석을 수행하고, 끝나면 카카오 callbackUrl로 결과를 보낸다."""

    try:
        result = await run_analysis(links, message)
    except Exception as e:
        # LLM/도구가 실패해도 응답은 나가야 한다는 원칙(CLAUDE.md 절대 원칙 3)과
        # 같은 이유로, 예상 못한 예외에도 빈손으로 끝내지 않는다.
        print(f"[ANALYSIS ERROR] {e}")
        result = kakao_response(
            "분석 중 문제가 발생했습니다. 잠시 후 다시 시도해주세요."
        )
    finally:
        if user_id:
            RUNNING_USERS.discard(user_id)

    try:
        async with httpx.AsyncClient() as client:
            await client.post(callback_url, json=result, timeout=10.0)
    except Exception as e:
        # 카카오 콜백은 "제한된 임시 기능"이라 실패·만료될 수 있다(CLAUDE.md 제약).
        # 지금은 로그만 남긴다 — 사용자가 재요청했을 때 결과를 돌려주려면
        # 위 TODO(job 저장소 + 조회 API)가 먼저 있어야 한다.
        print(f"[CALLBACK SEND FAILED] {e}")


def kakao_response(text: str):
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


def parse_urlscan_result(result: dict):
    task = result.get("task", {})
    page = result.get("page", {})
    verdicts = result.get("verdicts", {})
    urlscan = verdicts.get("urlscan", {})

    return {
        "url": task.get("url"),
        "title": page.get("title"),
        "brands": urlscan.get("brands", [])
    }
