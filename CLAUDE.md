# CLAUDE.md

## 제품

스미싱(택배 사칭) 의심 문자를 받아 링크를 조사하고 **근거와 함께** 결과를 알려주는
카카오톡 챗봇. 위험하면 KISA 챗봇으로 인계한다.

## 절대 원칙

1. **판정은 결정적 로직이 한다.** 공식 도메인 화이트리스트 대조가 판정의 근거다.
   LLM에게 "이 링크 위험해?"라고 묻지 않는다.
2. **LLM의 역할은 네 가지뿐이다.** (a) 문자에서 구조화된 정보 추출,
   (b) 관측 자료에서 위험 신호를 **근거와 함께 제안**, (c) 판정 결과를 사람이
   읽을 문장으로 변환, (d) 어떤 분석을 돌릴지 도구 호출로 선택(필수 분석은 코드가
   강제한다, ADR 0012). **판정 자체는 하지 않는다.** LLM이 낸 신호는 근거가
   실제 입력에 존재하는지 검증한 것만 쓴다. 링크 문자는
   `ai/pipeline/results.py` 가 검증·조립하고 `services/result_card_selector.py`
   가 카드를 고른다. 링크 없는 문자와 통화는 `ai/case/verify.py` 를 통과한
   근거로 `ai/case/message_gate.py`·`gate.py` 가 결론을 낸다.
3. **LLM이 실패해도 응답은 나가야 한다.** 설명은 부가 기능이다.
   추출이 실패하면 `fallback_analysis()` 로 빈 결과를 내고 판단 보류 카드로 간다.
   통화·문자 결론 문장은 LLM 없이 `ai/case/explain.py` 템플릿으로 만든다.
4. **의존 방향은 `backend` → `ai` 한 방향.** `ai/` 안에서 `server` 를 import 금지.
   `ai/` 는 카카오를 몰라야 한다 (payload, 카드 형식 등).

## 제약

- **카카오 스킬 타임아웃 5초.** 예산 배분은 `docs/latency-budget.md` 참조. 이 파일이 기준이다.
- 스킬 URL은 HTTPS만. 로컬 개발은 ngrok.
- **카카오 콜백에 의존한다.** 자체 격리 수집과 문자·페이지 분석은 첫 응답 뒤에 수행한다.
  콜백은 1회성이므로 결과를 저장해 "결과 확인" 조회 폴백을 함께 둔다.
  공식 도메인 대조와 문자·페이지 분석 결과를 함께 사용한다.

## 컨벤션

- 브랜치 `feature/…` `fix/…` `refactor/…`, 커밋 `feat:` `fix:` `docs:` `refactor:` `test:`
- 상세: `docs/conventions.md`
- 결정 기록: `docs/adr/`

## 참조

- PRD: `docs/PRD.md`
- BE↔AI 결과 스키마: `contracts/schemas/` (절차: `docs/contracts.md`)
- `docs/archive/` 는 구현 완료·대체된 문서 보관소다. 참고·검색하지 않는다.
