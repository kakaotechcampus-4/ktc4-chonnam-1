# mocks

BE 없이 전 화면을 돌리기 위한 목 핸들러와 픽스처. 1차 개발 기간 동안 프론트의 주 실행 환경입니다.

## 픽스처

| 이름 | 용도 |
| --- | --- |
| `result-signals.json` | 결과 A — 관측됨, 불일치 있음 |
| `result-held.json` | 결과 B — 접근 실패, 판단 보류 |
| `result-weak.json` | 관측은 됐으나 불일치가 약함 (해석 없음) |
| `result-no-signals.json` | 이상 징후 없음 — "안전"이 아니라 "범위 내 미발견" |
| `limit-reached.json` | 조회 한도 도달 |
| `token-expired.json` | 진입 토큰 만료 |
| `injection.json` | 관측 본문에 "이전 지시를 무시하라"가 박힌 경우 |

## 규칙

- **실제 스미싱 문자·URL을 픽스처에 넣지 않습니다.** 전부 합성 문자와 팀 소유 모형 페이지 주소입니다.
- 픽스처는 `/contracts/schemas`를 따릅니다. 스키마가 바뀌면 픽스처가 먼저 깨져야 정상입니다.
- **`unverified`가 빈 픽스처를 정상 케이스로 만들지 마세요.** 그건 실패 케이스입니다.
