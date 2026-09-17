from fastapi import FastAPI, Request

from urlscan_service import submit_url_scan, wait_for_url_scan_result
from url_utils import split_message


app = FastAPI()


@app.post("/kakao/skill")
async def kakao_skill(request: Request):

    body = await request.json()

    utterance = body.get(
        "userRequest", {}
    ).get("utterance", "")

    print(f"[KAKAO] {utterance}")

    links, message = split_message(utterance)

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
