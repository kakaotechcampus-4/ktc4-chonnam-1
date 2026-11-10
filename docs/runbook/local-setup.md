# 로컬 세팅

## 1. 도구

```bash
sudo apt update
sudo apt install -y git curl build-essential gh python3 python3-venv
```

Python 3.11 이상이 필요합니다 (`ai/pyproject.toml`).
VS Code + WSL 확장. **레포 루트를 여세요** (`code .`). 하위 폴더만 열면 git과
CLAUDE.md가 안 잡힙니다.

## 2. clone

```bash
git clone git@github.com:kakaotechcampus-4/ktc4-chonnam-1.git
cd ktc4-chonnam-1
cp .env.example .env      # 값은 팀 시크릿 저장소에서
python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` 는 `requirements.txt`(FastAPI·uvicorn·httpx, `-e ./ai`)에
pytest·pytest-asyncio·ruff 를 더합니다. 린트는 `ruff check .` 로 돌립니다(규칙은 `ruff.toml`). `ai` 패키지는 편집 가능 모드로 같이 설치됩니다.

**`APP_ENV=local` 을 설정하세요.** `APP_ENV` 기본값은 `production` 이라
`KAKAO_SKILL_SECRET` 없이 띄우면 `/kakao/skill` 이 모두 401 로 막힙니다
(`main.py` 의 `verify_skill_secret`). `local`·`test` 일 때만 스킬 비밀값 검사를 건너뜁니다.

## 3. 실행

```bash
python -m uvicorn main:app --reload --port 8000 --env-file .env
```

서버 코드는 `.env` 를 직접 읽지 않습니다. `--env-file` 로 넘기거나 셸 환경변수로 설정하세요.

테스트는 BE·AI 를 나눠 돌립니다.

```bash
python -m pytest tests -q
python -m pytest ai/tests -q
```

## 4. 카카오 연결 (배포 담당만)

```bash
ngrok http 8000
```
나온 HTTPS 주소 + `/kakao/skill` 을 챗봇 관리자센터 스킬 URL에 등록.

**평소에는 카톡에 붙이지 않습니다.** 스킬 URL은 챗봇당 하나라 여러 명이 동시에
못 씁니다. 카카오 payload 를 로컬에 직접 쏴서 개발하세요.

```bash
curl -s localhost:8000/kakao/skill -H 'Content-Type: application/json' \
  -d '{"userRequest": {"utterance": "결과 확인", "user": {"id": "local-user"}}}'
```

`callbackUrl` 이 없으면 콜백 없이 동기로 처리합니다. 실제 payload 모양은
`tests/` 의 흐름 테스트(`test_read_message_flow.py`, `test_smishing_card_flow.py`,
`test_voice_message_flow.py` 등)에 있습니다.
