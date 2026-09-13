import json

from fastapi import FastAPI, Request

app = FastAPI(
    title="Message Verify Server",
    description="메세지 링크 보안 검사 테스트 API",
    version="0.1.0"
)


@app.get("/")
def root():
    return {
        "message": "Message Verify Server is running!"
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok"
    }


@app.get("/test")
def test():
    return {
        "message": "Render + FastAPI connection success"
    }

@app.post("/kakao/skill")
async def kakao_skill(request: Request):
    body = await request.json()

    utterance = body.get("userRequest", {}).get("utterance", "")

    print(f"[KAKAO] {utterance}")

    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": f"서버가 받은 메시지:\n{utterance}"
                    }
                }
            ]
        }
    }
