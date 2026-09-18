# ADR 0004 — LLM 호출 예산은 호출 단계가 소유한다

상태: 채택 (2026-09-18)

## 맥락

`analyze.py` 의 `_create_client()` 가 클라이언트 타임아웃에 자기 모듈 상수
`TIMEOUT_SECONDS = 1.5` 를 박아 넣고, `signals.py` 와 `explain.py` 가 그 함수를
그대로 재사용했다. 두 모듈은 각자 `TIMEOUT_SECONDS = 2.0` 을 선언했지만
`asyncio.wait_for` 가 2.0 초를 허용해도 SDK 가 1.5 초에 먼저 끊었다.

`docs/latency-budget.md` 는 ③a 위험 신호 2.0s, ③b 설명 생성 2.0s 로 규정한다.
문서 셋이 2.0 을 말하고 코드만 1.5 로 동작했다.

구현 계획서가 "signals 와 explain 은 2.0" 과 "`_create_client()` 를 재사용한다"를
따로 적었고, 두 지시가 동시에 성립할 수 없다는 점을 아무도 보지 못했다.

## 결정

`ai/src/ai/llm/_client.py` 를 두고 `create_client(timeout)` 이 예산을 인자로
받는다. 각 호출 단계가 자기 `TIMEOUT_SECONDS` 를 넘긴다. 팩토리는 값을 모른다.

## 이유

- 예산은 단계의 성질이지 클라이언트의 성질이 아니다. 단계마다 다르다.
- 공유 팩토리에 값이 있으면 재사용이 곧 예산 덮어쓰기가 된다. 호출부만 읽어서는
  실제 타임아웃을 알 수 없다.
- `analyze` 의 예산을 조정하면 다른 두 단계가 조용히 따라 움직였다. 한 단계의
  성능 조정이 다른 단계의 폴백률을 바꾸는 결합은 추적이 불가능하다.

## 결과

- `analyze.py` 의 `_create_client()` 와 `_required_env()` 는 사라졌다. 세 모듈이
  private 함수를 가로질러 import 하던 것도 함께 정리됐다.
- `max_retries=0` 은 팩토리에 남긴다. 재시도는 단계와 무관하게 금지다. 한 번만
  재시도해도 단계 예산이 배가 되어 카카오 5초 SLA 를 넘긴다.
- `ai/tests/test_client.py` 가 선언된 예산이 클라이언트까지 도달하는지 단언한다.
  값을 다시 팩토리에 고정하면 테스트가 깨진다.
- 예산 값을 바꿀 때는 `docs/latency-budget.md` 를 먼저 고친다. 그 파일이 기준이다.
