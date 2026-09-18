# backend/src/server/models

ORM 모델. `/infra/migrations`에서 정의한 테이블과 1:1 대응합니다.

## 테이블 목록

| 테이블 | 용도 |
| --- | --- |
| `jobs` | 분석 작업 상태·조회 횟수 |
| `job_steps` | 진행 체크리스트 (실패도 유지) |
| `job_lookup_log` | AI ⑤ 호출 감사 로그 |
| `chatbot_sessions` | 카카오 대화 세션 (중복 전송 방지) |
| `message_parses` | 문자 해석·분류 결과 |
| `observations` | 관측 결과 (성공·실패 모두) |
| `evidence_cards` | 근거 카드 4칸 |
| `official_routes` | 공식 확인 경로 |
| `web_entry_tokens` | 2차 웹 진입 토큰 |
| `norm_rules` / `case_examples` / `signal_defs` / `risk_types` | RAG 지식베이스 (AI 스키마 채택, BE는 인프라만) |
| `verification_channels` / `report_channels` | KISA 등 고정 확인·신고 채널 |

## 작성 원칙

- 필드명은 `/contracts/schemas`와 완전히 통일합니다.
- `evidence_cards.verdict`, `jobs.status` 등 enum에는 **`safe` 값을 추가하지 않습니다** — 타입 레벨 가드레일입니다.
- **동의/고지 관련 필드(`consent_id`, `notice_log` 등)는 만들지 않습니다** (PM 결정 유지).
