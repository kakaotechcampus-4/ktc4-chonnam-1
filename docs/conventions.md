# 컨벤션

## 브랜치

- `main` — 보호됨. 직접 push 금지, PR 승인 1명 필요
- `feat/기능명` `fix/버그명` — 작업 단위로 생성 (사람 단위 아님)
- 한 브랜치는 2~3일 안에 끝낼 크기로

## 커밋

`feat:` `fix:` `docs:` `refactor:` `test:` `chore:` 접두어 + 한글 설명

## PR

- 작을수록 좋음
- 작업 전 항상 `git switch main && git pull`

## 공용 파일 오너

같이 건드리는 파일은 오너에게 먼저 말하기.

| 파일 | 오너 |
|---|---|
| `ai/src/ai/types.py` | (미정) |
| `backend/src/server/data/whitelist.yaml` | (미정) |
| `docs/latency-budget.md` | (미정) |
