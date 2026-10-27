# contracts

카카오 payload 스키마. 외부(카카오)와의 실제 계약만 둡니다.

BE↔AI 타입은 여기 없습니다 — `ai/src/ai/types.py` 가 소유합니다.
(호출당하는 쪽이 입력 타입을 정의한다.)

## 스키마

| 파일 | 만드는 쪽 | 쓰는 쪽 |
| --- | --- | --- |
| `schemas/voice-case-result.schema.json` | `ai.case.analyze_call` 의 반환 `VoiceCaseResult` (`ai/src/ai/case/types.py`)에서 생성 | 백엔드. 결과를 저장하고 카카오 콜백 카드와 `결과 확인` 조회로 보낼 때 읽는다 |

위 파일은 카카오 계약이 아니라 BE↔AI 결과 형태를 BE가 참고하도록 내보낸 사본이다.
원본은 `VoiceCaseResult` 이고, `ai/tests/test_voice_schema_contract.py` 가 둘이 어긋나면 실패한다.
모델을 바꾸면 그 파일을 직접 실행해 스키마를 다시 만든다.
