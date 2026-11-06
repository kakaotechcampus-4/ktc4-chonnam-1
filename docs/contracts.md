# 스키마 변경 절차

`contracts/schemas/` 는 **BE↔AI 결과 스키마**를 담습니다 (`contracts/README.md`).
카카오 payload 스키마는 아직 여기 없습니다.

| 파일 | 원본 모델 | 지키는 테스트 |
| --- | --- | --- |
| `voice-case-result.schema.json` | `VoiceCaseResult` (`ai/src/ai/case/types.py`) | `ai/tests/test_voice_schema_contract.py` |
| `message-case-result.schema.json` | `MessageCaseResult` (`ai/src/ai/case/types.py`) | `ai/tests/test_message_schema_contract.py` |

원본은 pydantic 모델이고 JSON 스키마는 BE가 참고하도록 내보낸 사본입니다.
JSON 파일을 손으로 고치지 않습니다.

## 변경 시

1. `ai/src/ai/case/types.py` 의 모델을 고친다
2. 저장소 루트에서 해당 테스트 파일을 직접 실행해 스키마를 다시 만든다
   (`model_json_schema(mode="serialization")` 결과에 `$schema`·`$id`·`title`·`description` 을 붙여 덮어쓴다)
   ```bash
   python -X utf8 ai/tests/test_voice_schema_contract.py
   python -X utf8 ai/tests/test_message_schema_contract.py
   ```
3. `python -m pytest ai/tests/test_voice_schema_contract.py ai/tests/test_message_schema_contract.py -q`
   로 커밋할 스키마가 모델과 같은지 확인한다. 어긋나면 `test_committed_schema_matches_model` 이 실패한다
4. 모델 변경과 다시 만든 스키마를 같은 PR 에 올린다. 결과를 읽는 BE 코드(`main.py`, `services/`)도 함께 맞춘다
