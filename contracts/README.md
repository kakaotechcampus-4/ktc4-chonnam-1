# contracts

BE↔AI 결과 스키마(`message-case-result`, `voice-case-result`)를 둡니다. 카카오 payload 스키마는 아직 없습니다. 변경 절차는 `docs/contracts.md`.

타입의 원본은 `ai/` 가 소유합니다(호출당하는 쪽이 정의한다). 여기 있는 것은 BE 가 참고하도록 내보낸 사본입니다.

## 스키마

| 파일 | 만드는 쪽 | 쓰는 쪽 |
| --- | --- | --- |
| `schemas/voice-case-result.schema.json` | `ai.case.analyze_call` 의 반환 `VoiceCaseResult` (`ai/src/ai/case/types.py`)에서 생성 | 백엔드. 결과를 저장하고 카카오 콜백 카드와 `결과 확인` 조회로 보낼 때 읽는다 |
| `schemas/message-case-result.schema.json` | `ai.case.analyze_message_case`·`apply_message_answer` 의 반환 `MessageCaseResult` (`ai/src/ai/case/types.py`)에서 생성 | 백엔드. 링크 없는 문자 결과(R6b)를 저장하고 Q1·Q2 답을 받을 때 다시 넘긴다 |

위 파일은 카카오 계약이 아니라 BE↔AI 결과 형태를 BE가 참고하도록 내보낸 사본이다.
원본은 `VoiceCaseResult`·`MessageCaseResult` 이고, `ai/tests/test_voice_schema_contract.py`·`test_message_schema_contract.py` 가 어긋나면 실패한다.
모델을 바꾸면 그 파일을 직접 실행해 스키마를 다시 만든다.
