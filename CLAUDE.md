# CLAUDE.md

## 제품

스미싱(택배 사칭) 의심 문자를 받아 링크를 조사하고 **근거와 함께** 결과를 알려주는
카카오톡 챗봇. 위험하면 KISA 챗봇으로 인계한다.

## 절대 원칙

1. **판정은 결정적 로직이 한다.** 공식 도메인 화이트리스트 대조가 판정의 근거다.
   LLM에게 "이 링크 위험해?"라고 묻지 않는다.
2. **LLM의 역할은 두 가지뿐이다.** (a) 난독화된 문자에서 URL 추출,
   (b) 판정 결과를 사람이 읽을 문장으로 변환.
3. **LLM이 실패해도 응답은 나가야 한다.** 설명은 부가 기능이다.
   타임아웃 시 `ai/llm/explain.py` 의 템플릿으로 폴백한다.
4. **의존 방향은 `backend` → `ai` 한 방향.** `ai/` 안에서 `server` 를 import 금지.
   `ai/` 는 카카오를 몰라야 한다 (payload, 카드 형식 등).

## 제약

- **카카오 스킬 타임아웃 5초.** 예산 배분은 `docs/latency-budget.md` 참조. 이 파일이 기준이다.
- 스킬 URL은 HTTPS만. 로컬 개발은 ngrok.
- 카카오 콜백은 제한된 임시 기능이라 의존하지 않는다.

## 컨벤션

- 브랜치 `feat/…` `fix/…`, 커밋 `feat:` `fix:` `docs:` `refactor:`
- 상세: `docs/conventions.md`
- 결정 기록: `docs/adr/`

## 참조

- PRD: `docs/PRD.md`
- 카카오 payload 스키마: `contracts/schemas/`
