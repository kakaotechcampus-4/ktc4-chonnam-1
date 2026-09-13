from fastapi import FastAPI

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
