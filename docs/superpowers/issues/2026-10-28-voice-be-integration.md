# [BE] 통화 녹음 보이스피싱 분석 연동 (feature/voice-fishing-ai 후속)

## 무엇

`feature/voice-fishing-ai` 에서 AI 쪽 통화 분석을 마쳤습니다. 수신·다운로드·콜백 경로는 PR #105(`feature/voice-fishing-be`)가 `mock_analyze_voice_case()` 목업으로 이미 만들었습니다. 남은 일은 그 목업을 아래 함수로 바꾸고, 카드를 `VoiceCaseResult` 에 맞추는 것입니다. 흐름은 `docs/designs/voice-phishing-flow.md`(FE 초안), 시간 예산은 `docs/latency-budget.md` 통화 행, 콜백 구조는 `docs/adr/0003-kakao-callback.md` 를 따릅니다.

AI 진입점 (`ai.case`)

| 함수 | 역할 |
| --- | --- |
| `transcribe_call(audio: bytes, filename: str, *, runtime=None) -> TranscriptResult` | whisper STT. 문장마다 세그먼트 하나이고 화자는 나누지 않음([ADR 0007](../../adr/0007-no-speaker-diarization.md)). 내부 한도 30초 |
| `analyze_call(transcript, *, runtime=None, time_budget_s=40.0) -> VoiceCaseResult` | 주장 읽기 + 사례 검색 + 결정적 판정 |
| `warm_up_voice_case_search()` | 사례 색인 미리 적재 (`from ai.case import warm_up_voice_case_search`. `ai.kb.search` 의 같은 이름 함수는 색인만 채우고 관문 모델은 읽지 않는다) |
| `apply_call_answers(result, answers) -> VoiceCaseResult` | 쌓인 Q1 답으로 `response_actions` 를 채운 사본 (v3 §8). 결론·멈출 행동은 바뀌지 않음. LLM·네트워크 0회라 콜백 없이 바로 답해도 됨 |
| `response_actions_for(answers, evidence)` (`ai.case.actions`) | 위 함수가 쓰는 Q1 답 -> 피해 대응 목록 |
| `default_response_actions(option_id)` (`ai.case.actions`) | 저장된 결과를 못 찾았을 때 쓰는 대응 (v3 §7.7 마지막 표) |

결과 형태: `contracts/schemas/voice-case-result.schema.json` (`VoiceCaseResult` 에서 생성, 어긋나면 AI 테스트가 실패합니다).

## 흐름

1. 녹음은 두 모양으로 옵니다. 둘 다 받아야 합니다.
   - 카톡 음성메시지(마이크 버튼): `userRequest.params.media.type == "audio"`, 주소는 `media.url`. PR #105 가 실기기로 확인했고 이 경로만 구현돼 있습니다.
   - 파일로 첨부한 m4a: `params.media` 없이 `userRequest.utterance` 에 녹음 URL(`talk.kakaocdn.net/…m4a?credential=…&signature=…`)만 옵니다(`voice-phishing-flow.md` 0절). 호스트 + 경로 확장자로 판별해야 하며 PR #105 에는 아직 없습니다.
2. BE가 URL을 검증하고 내려받습니다.
   - 호스트는 카카오 CDN만 허용 (PR #105 `media_guard.py`, `KAKAO_MEDIA_HOSTS`)
   - 크기 상한 약 25MB (whisper 한도), 형식 m4a·mp3·wav·webm. Content-Type 은 `etc/m4a` 처럼 표준이 아니므로 확장자로 판단
   - 초과·형식 불가는 분석 없이 안내 카드
3. **첫 응답은 즉시** 보냅니다 (콜백 사용, 5초 안). 이후 작업은 콜백 경로에서 수행합니다.
4. `transcribe_call(audio, filename)`. 걸린 시간(`elapsed`)을 잽니다.
5. `analyze_call(transcript, time_budget_s = 40 - elapsed)`. PR #105 는 다운로드부터 카드 렌더링까지를 `asyncio.wait_for(..., CALLBACK_DEADLINE_SECONDS=45)` 로 감쌉니다. 다운로드(한도 15초)·STT도 이 45초 안에 들어가므로, 분석 예산은 v3 B3 대로 `CALLBACK_DEADLINE_SECONDS - 5`(40초)에서 `elapsed` 를 뺍니다. `45 - elapsed` 로 주면 분석이 45초를 다 써서 카드 렌더링 전에 바깥 `wait_for` 가 작업을 취소하고 결과가 시간 초과(R4)로 바뀝니다. `elapsed` 는 다운로드 시작부터 잽니다. 남은 예산이 너무 적으면(예: 5초 미만) 호출하지 않고 대본 없음/미확인 카드로 처리합니다.
6. 결과를 **먼저 저장** (콜백은 1회성) -> 콜백 카드 전송. 콜백이 만료·실패하면 `결과 확인` 조회가 저장본을 보여줍니다 (처리 중이면 K1, 실패면 R4, 없으면 K4).
7. 결과 카드 버튼은 `chatbot-copy.md` V1(PR #106)을 따릅니다: `112 신고`·`자세히 보기`. 신고·상담 버튼은 결과 카드가 아니라 Q1 → K6 로 안내합니다(D15). 녹음의 상담 창구는 `1394 상담`입니다(D17). PR #105 의 `r-voice-result.json` 의 `신고·상담하기` 버튼은 이 기준으로 바꿔야 합니다.

한 사용자가 분석 중에 또 보내면 K5 (문자와 같은 잠금 규칙).

## 기동 시

`backend/src/server/lifespan.py` 에서 `warm_up_kb()` 옆에 `warm_up_voice_case_search()` 를 부릅니다. 콜드 적재가 약 1.4초라 1초 검색 예산을 넘기기 때문입니다(같은 호출이 사례 첨부 관문 모델도 미리 읽습니다. 약 30ms, 관문 모델 적재 실패는 로그만 남기고 기동은 계속하며 그 경우 사례는 붙지 않고 `similar_cases` 는 미확인이 됩니다). 실패해도 서버는 떠야 하고(기존 `warm_up_kb` 와 같은 처리), 부르지 않으면 첫 통화 요청이 적재 비용을 떠안아 `similar_cases:timeout` 이 됩니다.

## 환경 변수

엘리스 ML API 는 모델마다 Base URL 이 다릅니다.

| 이름 | 용도 | 기본값 |
| --- | --- | --- |
| `STT_BASE_URL` | whisper 엔드포인트 루트. **`/v1` 없이** 넣습니다(코드가 `/v1/audio/transcriptions` 를 붙임). 예: `https://mlapi.run/<엔드포인트 ID>` | 필수 |
| `STT_MODEL` | STT 모델 | `whisper-large-v3` |
| `LLM_API_KEY` | 위 호출 공용 키 | 필수 |
| `LLM_BASE_URL` / `LLM_MODEL` / `CLAIMS_MODEL` | 주장 읽기 (기존, 읽기만). `LLM_BASE_URL` 은 OpenAI 클라이언트용이라 지금 Render 값처럼 **`/v1` 까지** 넣습니다. `STT_BASE_URL` 과 형식이 다르니 값을 복사하지 마세요. `CLAIMS_MODEL` 이 없으면 `LLM_MODEL` 을 씁니다 | 기존 값 |

`DIARIZE_BASE_URL`·`DIARIZE_MODEL` 은 화자 분리를 없애면서(ADR 0007) 쓰지 않습니다. 이미 넣었다면 지워도 됩니다.

Render 환경 변수와 `.env.example` 에 추가해야 합니다.

## 결과 -> 카드 매핑

| `VoiceCaseResult` | 카드 |
| --- | --- |
| `conclusion` (`suspected` / `inconclusive` / `not_analyzable` / `input_required`. 통화에는 `official_domain` 이 나오지 않음) | V1 결과 카드 종류 선택 |
| `caution` | 주의 문구 덧붙임 여부 |
| `stop_actions` | 지금 멈출 행동 목록 |
| `claims`, `evidence` (`quote`, `origin`) | 근거 줄. 원문 인용만 보여주고 LLM 문장으로 바꾸지 않음 |
| `number_checks` | 상대가 말한 번호의 공식 대표번호 대조 줄 (`org_not_listed` 는 "공식이 아니다"가 아님) |
| `official_contact` (`org`, `number`, `source_url`) | V1 `verify_line`: `{org} 대표번호({number})로 직접 전화해 확인하세요.` 없으면(`null`) `상대가 말한 기관의 대표번호를 직접 찾아 전화해 확인하세요.` 번호는 대표번호 KB 에서만 오며 녹음 속 번호가 아님 |
| `response_actions` | K6 대응 줄. 분석 직후에는 비어 있고 `apply_call_answers` 가 채움 |
| `checked` / `unchecked` (+`reason`) | 확인한 것 / 확인하지 못한 것. 둘을 섞지 않음 (`not_run` 과 `failed` 구분) |
| `failures` | 실패 이유 문구 (`timeout`, `llm_error`, `invalid_output` 등) |
| `rejected` (`reason`) | 버린 주장. `self_side` 는 받는 사람이 되묻거나 옮긴 말·자기 일을 말한 문장으로 본 것(ADR 0007). 카드에 싣지 않음 |
| `similar_cases` | 비슷한 사례 (있을 때만). 사례 첨부 관문 점수가 낮으면 유사도가 높아도 비어 있을 수 있습니다. 가족·납치 유형 사례의 `similarity` 에는 지인 어휘 가점(+0.03)이 포함돼 원점수보다 조금 높습니다. 악성 확률로 표시하지 마세요 |
| `explanation` | 부가 설명. 비어 있어도 카드는 나가야 함 |
| `questions[0]` (Q1, `option_ids`: `entered_info`, `installed_app`, `sent_money`, `not_yet`) | 결과 아래 `이미 하신 게 있나요?` 리스트 카드. 라벨은 FE |

Q1 답은 쌓입니다. 답이 들어올 때마다 저장된 결과와 지금까지의 답으로 `apply_call_answers(result, answers)` 를 불러 다시 저장하고, `response_actions` 로 K6 카드를 만듭니다(비어 있으면 A2). 순서는 함수가 정한 순서를 그대로 씁니다.

대응 값은 FE D17(`chatbot-copy.md` 3절)을 따릅니다. `call_1332` 는 없습니다.

| Q1 답 | `response_actions` |
| --- | --- |
| `sent_money` (상품권 요구 있음) | `contact_gift_card_issuer`, `report_112` |
| `sent_money` (그 밖) | `request_payment_stop`, `report_112` |
| `entered_info` ("주민" 요구 있음) | `register_info_exposure` |
| `entered_info` (그 밖) | `change_password`, `register_info_exposure` |
| `installed_app` | `request_payment_stop`, `isolate_phone`, `use_other_device`, `remove_app_and_check_device` |

K6 버튼은 `report_112` 에만 `112 신고`, 버튼 달 대응이 없으면 `1394 상담`, `installed_app` 만 고른 경우는 버튼 없음(D17).

## 개인정보

- 분석 경로의 `segments` 는 정규식으로 가려진 상태입니다 (`mask_pii`). 기관명·전화번호는 근거로 써야 해서 남깁니다.
- 대본을 **저장하거나 외부로 보내기 전**에는 주입형 `EliceMasker` (`ai/src/ai/case/privacy.py`)로 한 번 더 가립니다. 엔드포인트 주소는 아직 미정이라 `base_url`/`api_key`/`timeout` 을 설정으로 받게 하고, 미정이면 대본은 저장하지 않고 결과(`VoiceCaseResult`)만 저장합니다.
- 녹음 원본은 분석 직후 폐기합니다 (저장 금지). 녹음 전용 전송 동의(W3v)는 FE 초안 VF4 를 따릅니다.

## 알려진 한계 / 후속

- 일부 오디오 바이트가 Cloudflare 403 으로 막힙니다. voice-stt 참고 구현은 gain 0.99 -> 0.97 로 바꿔 재시도합니다. 디코딩이 필요해서 BE 쪽 일입니다 (AI는 바이트만 받음).
- `audio_seconds` 는 wav 만 채워집니다. 그 외 형식은 `None` 입니다.
- 지식베이스 대본의 라이선스 문제: 금감원 자료는 "무단 복제·배포 금지"입니다. 공개 저장소에 올리기 전에 해결해야 합니다.
- 가족·지인 사칭 유형은 사례 검색이 약합니다 (결과가 `inconclusive` 로 나올 수 있음). 사례 첨부 관문 때문에 `similar_cases` 가 빈 채로 나가는 것은 오류가 아닙니다.
- 5초 안에 카카오 첫 응답, STT 실측은 2~11초입니다(화자 분리를 하던 때 3~4분 녹음 합계 12~16초). 더 긴 녹음의 한도는 정해지지 않았습니다.
- 대본에 화자 구분이 없습니다. 누가 한 말인지는 문장 꼴로 가늠하고, `suspected` 는 자기소개 꼴의 기관·지인 주장과 부탁 꼴의 요구가 함께 있을 때만 나옵니다(ADR 0007). 결과 형태는 바뀌지 않았습니다.

## 완료 조건

- [ ] 녹음 URL 검증(호스트·크기·형식)과 내려받기, 초과·불가 시 안내 카드 (PR #105 에서 음성메시지 경로 완료. 파일 첨부 utterance 경로 추가 필요)
- [ ] `mock_analyze_voice_case()` 와 `render_voice_result_card()` 를 `VoiceCaseResult` 기준으로 교체
- [ ] 첫 응답 즉시, 이후 `transcribe_call` -> `analyze_call(time_budget_s = 40 - elapsed)` 를 콜백 경로에서 실행
- [ ] 결과를 콜백 전에 저장하고, `결과 확인` 이 저장본(처리 중 K1 / 실패 R4 / 없음 K4)을 보여줌
- [ ] V1 결과 카드(`official_contact` 로 `verify_line`)와 Q1 리스트 카드 렌더링, Q1 답은 `apply_call_answers` 결과로 K6 생성
- [ ] V1 버튼 `112 신고`·`자세히 보기`, K6 빈 버튼 자리 `1394 상담` (D15·D17)
- [ ] lifespan 에서 `warm_up_voice_case_search()` 호출 (실패해도 기동)
- [ ] 환경 변수 4개(`STT_BASE_URL`·`STT_MODEL`·`LLM_API_KEY`·`CLAIMS_MODEL`)를 `.env.example`·Render 에 반영
- [ ] 대본 저장·외부 전송 전 마스킹 (엔드포인트 확정 전에는 대본 비저장)
- [ ] STT 실패(`not_analyzable`), 시간 초과 경로 테스트
- [ ] 한 사용자 동시 요청 K5 잠금
- [ ] 의존 방향 유지 (`backend` -> `ai`), `ai/` 는 카카오 payload 를 모름
- [ ] 관련 테스트와 전체 테스트 통과
