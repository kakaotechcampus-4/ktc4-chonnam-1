# 로컬 세팅

## 1. 도구

```bash
sudo apt update
sudo apt install -y git curl build-essential gh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

VS Code + WSL 확장. **레포 루트를 여세요** (`code .`). 하위 폴더만 열면 git과
CLAUDE.md가 안 잡힙니다.

## 2. clone

```bash
git clone git@github.com:<조직명>/smishing-bot.git
cd smishing-bot
cp .env.example .env      # 값은 팀 시크릿 저장소에서
cd backend && uv sync
```

## 3. 실행

```bash
uv run uvicorn server.main:app --reload --port 8000
```

## 4. 카카오 연결 (배포 담당만)

```bash
ngrok http 8000
```
나온 HTTPS 주소 + `/skill` 을 챗봇 관리자센터 스킬 URL에 등록.

**평소에는 카톡에 붙이지 않습니다.** 스킬 URL은 챗봇당 하나라 여러 명이 동시에
못 씁니다. `backend/tests/fixtures/` 의 payload를 로컬에 쏴서 개발하세요.
