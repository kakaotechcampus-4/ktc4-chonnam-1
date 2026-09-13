# backend

FastAPI 스킬 서버. 카카오 웹훅을 받아 판정하고 응답 카드를 돌려줍니다.

**판정은 여기서 합니다.** 공식 도메인 화이트리스트 대조가 판정 근거입니다.
LLM은 `ai` 패키지를 통해 설명 문장만 받아옵니다.

```bash
uv sync
uv run uvicorn server.main:app --reload --port 8000
```
