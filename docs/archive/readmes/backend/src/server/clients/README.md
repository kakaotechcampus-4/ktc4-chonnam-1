# backend/src/server/clients

AI 파트를 호출하는 유일한 창구. **이 폴더 밖에서는 AI를 호출하지 않습니다** (N05).

## 파일 계획

| 클라이언트 함수 | 대응하는 AI 스펙 | 호출 시점 |
| --- | --- | --- |
| `call_parse(raw_text)` | AI-01, AI-02 (① 해석+유형분류) | 문자 수신 직후, 1회 |
| `call_compare(observations, matched_rules)` | AI-04(RAG 검색) → AI-07(대조) | 관측 완료 후, 1회. `observations`가 비면 호출 자체를 하지 않음 (AI-08) |
| `call_investigate(job_state)` | AI-11 (⑤ 보강 판단) | 근거 부족 시, 루프당 1회. 조회 한도 확인은 `orchestration/`이 **먼저** 함 |

## 지켜야 할 규칙

- 모든 요청/응답은 `/contracts/schemas`에 정의된 스키마로만 주고받습니다. 자유 텍스트 프롬프트를 이 폴더에서 조립하지 않습니다 (N09).
- 이 세 함수가 BE가 AI를 호출하는 전부입니다. 임베딩 호출(AI-N-03) 등 판단이 아닌 호출은 여기 포함되지 않습니다 — AI 파트 내부에서 처리됩니다.
- 응답을 받은 즉시 `orchestration/`으로 넘겨 검증받습니다. 이 폴더 자체는 검증하지 않습니다 (역할 분리).
