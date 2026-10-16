# Smishing Scenario 3 Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** 기존 기능으로 택배 사칭 문자 한 건의 입력·분석·결과·상세 보기·재조회를 연결한다.

**Architecture:** 기존 `analyze_message_part`·`finalize_analysis`, 메모리 작업 저장소,
카카오 카드·캐러셀을 재사용한다. 에이전트 루프·상세 웹·다른 피싱 유형은 추가하지 않는다.

**Tech Stack:** Python, FastAPI, pytest, 기존 카카오 JSON 템플릿.

**Spec:** 사용자와 합의한 최소 시나리오 및 브랜치·PR 승인 방식.
`docs/designs/agent-loop-plan-v2.md`는 후속 전환 설계이며 이번에 구현하지 않는다.

## Global Constraints

- 통합 브랜치: `refactor/scenario-message-test-3`; 파트 접미사: `-ai`, `-fe`, `-be`.
- 각 파트 PR은 통합 브랜치 대상. 사용자의 승인 전에는 merge하지 않는다.
- Render·카카오의 Chrome 설정 변경은 변경안을 보고하고 승인받은 뒤 수행한다.
- 판정 규칙·공개 AI API·의존 방향을 유지한다. 새 의존성을 추가하지 않는다.
- 자동 검증은 합성 문자와 외부 응답 대체를 사용한다. 실제 연결 검증과 구분한다.
- 테스트용 `.example` 주소는 오프라인 전용이다. 실제 urlscan에 제출하지 않는다.

## Review Focus

- 신호 분석 실패 시 사칭 기관·요구 행동 같은 확보된 사실이 유지되는가.
- 원문에 없는 위험 근거는 최종 결과에서 제외되는가.
- 이미지 캐러셀의 두 줄 제한 때문에 근거가 숨겨지지 않는가.
- 공식 확인 안내의 고객센터 번호가 128자 제한에서 잘리지 않는가.
- 콜백 실패 후에도 동일 사용자의 저장된 결과·상세 보기가 재분석 없이 열리는가.

## Task 1 — AI (`-ai`)

- [x] `ai/tests/test_smishing_scenario.py`: 실제 AI 파이프라인에 SDK 응답만 주입한다.
- [x] 정상 인용·허위 인용·신호 분석 timeout의 결과를 검증한다.
- [x] `ai/README.md`에 실행법과 실제 모델 검증의 한계를 기록한다.
- [x] `python -X utf8 -m pytest ai/tests -q`: 961 passed, 1 skipped.
- [ ] 커밋·PR 생성.

## Task 2 — FE (`-fe`)

- [ ] `c-detail-carousel.json`, `c4-guidance.json`: 상세를 textCard 캐러셀로 통일한다.
- [ ] `r8-not-official.json`: 기존 자세히 보기 블록으로 가는 버튼을 연결한다.
- [ ] 템플릿·공식 문구·관리자센터 연결 안내를 함께 갱신한다.
- [ ] 기존 카드 렌더링 테스트와 시나리오 카드 규격을 검증한 뒤 커밋·PR 생성.

## Task 3 — BE (`-be`)

- [ ] 공식 안내를 128자 단위로 나누되 전화번호를 끊지 않는 회귀 테스트를 먼저 실행한다.
- [ ] `services/result_card_renderer.py`에서 기존 안내 카드 생성만 수정한다.
- [ ] 외부 HTTP/SDK만 대체해 입력·콜백·저장·상세·재조회 통합 테스트를 작성한다.
- [ ] `.env.example`과 `docs/experiments/2026-10-17-smishing-scenario-3.md`에 배포·휴대폰 검증 절차를 기록한다.
- [ ] 전체 테스트 후 커밋·PR 생성. Chrome 변경안은 실제 설정 확인 전까지 제안으로 표시한다.

## Acceptance

파트 PR 생성까지 자동 수행한다. merge·배포·Chrome 설정 변경·실제 휴대폰 검증 완료는
각각 승인 및 실제 실행 증거가 있어야 완료로 기록한다.
