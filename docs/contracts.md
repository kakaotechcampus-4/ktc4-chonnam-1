# 스키마 변경 절차

`contracts/schemas/` 는 **카카오 payload 스키마**만 담습니다.
(BE↔AI는 함수 호출이라 계약이 불필요합니다. 타입은 `ai/src/ai/types.py` 소유.)

## 변경 시

1. 스키마 수정 PR
2. `backend/src/server/models/` 갱신
3. `backend/tests/fixtures/` 샘플 갱신
