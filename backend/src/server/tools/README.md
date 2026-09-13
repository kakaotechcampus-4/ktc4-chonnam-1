# backend/src/server/tools

**Q-AI1의 답이 여기 있습니다: 외부 도구는 BE가 실행하고, AI는 "어떤 도구를 쓸지"만 판단합니다.**

AI의 ⑤(보강 판단) 응답에는 `next_lookup`(예: `external_scan_retry`, `domain_lookup`, `file_reputation_check`) 필드가 있습니다. `orchestration/`은 이 값을 읽어서 이 폴더의 해당 함수를 호출합니다. **AI는 함수를 직접 실행하지 않고, 어떤 함수를 실행할지 이름만 돌려줍니다.**

## 도구 목록

| 도구 | 역할 | 비고 |
| --- | --- | --- |
| `external_scan_retry` | 외부 격리 분석 API 재시도 | 실패도 정상 결과로 저장 (B07) |
| `domain_lookup` | WHOIS 등 도메인 등록 정보 조회 | 결정론적 신호 평가(AI-06)의 입력으로도 쓰임 |
| `file_reputation_check` | 파일 평판 조회 | **파일을 실행하지 않는다** — 조회만 |

## 지켜야 할 규칙

- **허용된 도구 목록은 이 폴더에 고정된 함수 집합입니다.** AI가 목록에 없는 도구 이름을 반환하면 무시하고 `held`로 처리합니다 — AI가 임의의 도구를 만들어 호출할 수 없습니다.
- 모든 도구 호출은 `orchestration/`이 관리하는 조회 횟수 카운트 안에서만 실행됩니다. 이 폴더의 함수 자체는 한도를 모르고, 호출 여부만 결정합니다.
- 도구 실행 결과(성공/실패 모두)는 `models/`의 `observations` 테이블에 저장됩니다.
