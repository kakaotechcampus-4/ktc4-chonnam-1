# [BE] 통화 녹음 보이스피싱 분석 연동 (feature/voice-fishing-ai 후속)

## 무엇

`feature/voice-fishing-ai` 에서 AI 쪽 통화 분석을 마쳤습니다. 수신·다운로드·콜백 경로는 PR #105(`feature/voice-fishing-be`)가 `mock_analyze_voice_case()` 목업으로 이미 만들었습니다. 남은 일은 그 목업을 아래 함수로 바꾸고, 카드를 `VoiceCaseResult` 에 맞추는 것입니다. 흐름은 `docs/designs/voice-phishing-flow.md`(FE 초안), 시간 예산은 `docs/latency-budget.md` 통화 행, 콜백 구조는 `docs/adr/0003-kakao-callback.md` 를 따릅니다.

AI 진입점 (`ai.case`)

| 함수 | 역할 |
| --- | --- |
| `transcribe_call(audio: bytes, filename: str, *, runtime=None) -> TranscriptResult` | whisper STT + Sonnet 5.5 화자 분리. 내부 한도 STT 30초 + 분리 20초 |
| `analyze_call(transcript, *, runtime=None, time_budget_s=40.0) -> VoiceCaseResult` | 주장 읽기 + 사례 검색 + 결정적 판정 |
| `warm_up_voice_case_search()` | 사례 색인 미리 적재 (`from ai.case import warm_up_voice_case_search`. `ai.kb.search` 의 같은 이름 함수는 색인만 채우고 관문 모델은 읽지 않는다) |
| `apply_call_answers(result, answers) -> VoiceCaseResult` | 쌓인 Q1 답으로 `response_actions` 를 채운 사본 (v3 §8). 결론·멈출 행동은 바뀌지 않음. LLM·네트워크 0회라 콜백 없이 바로 답해도 됨 |
| `response_actions_for(answers, evidence)` (`ai.case.actions`) | 위 함수가 쓰는 Q1 답 -> 피해 대응 목록 |
| `default_response_actions(option_id)` (`ai.case.actions`) | 저장된 결과를 못 찾았을 때 쓰는 대응 (v3 §7.7 마지막 표) |

결과 형태: `contracts/schemas/voice-case-result.schema.json` (`VoiceCaseResult` 에서 생성, 어긋나면 AI 테스트가 실패합니다).

## 흐름

1. 녹음은 두 모양으로 옵니다. 둘 다 받아야 합니다.
   - 카톡 음성메시지(마이크 버튼): `userRequest.params.media.type == "audio"`, 주소는 `media.url`. PR #105 가 실기기로 확인했고 구현돼 있습니다.
   - 파일로 첨부한 m4a: `params.media` 없이 `userRequest.utterance` 에 녹음 URL(`talk.kakaocdn.net/…m4a?credential=…&signature=…`)만 옵니다(`voice-phishing-flow.md` 0절). **구현 완료**(`main.py` `_utterance_voice_attachment()`) — 허용 호스트(`is_allowed_media_url`)와 경로 끝 오디오 확장자로 판별한다. `split_message()` 호출 전에 분기해 utterance를 링크로 오인하지 않는다.
2. BE가 URL을 검증하고 내려받습니다.
   - 호스트는 카카오 CDN만 허용 (PR #105 `media_guard.py`, `KAKAO_MEDIA_HOSTS`)
   - 크기 상한 25MB(`main.py` `MAX_VOICE_FILE_BYTES`, whisper 한도)는 **구현 완료**. 형식은 지금 코드에 m4a·mp3·wav·**webm**이 들어 있는데, `v0-voice-check.json`(FE 카드)은 "m4a, mp3, wav"만 안내한다 — webm 포함 여부가 코드와 카드 안내가 어긋난 상태다(아래 "FE/PM 확인 필요" 참고). **실제 카카오 음성 파일 형식·디코더 지원 범위를 확인하기 전까지 임의로 빼거나 넣지 않는다**
   - **형식·크기·길이 초과 시 전용 안내 카드(VE1·VE2)는 아직 구현 전**이다(2026-10-30 기준, 다른 담당자 작업 중). 지금은 크기 초과가 `MediaDownloadBlocked` → 일반 실패 카드(`r4-unavailable`)로만 나간다. 녹음 길이 상한 자체도 아직 정해지지 않았다(VE2 `reason_line` 문구는 상한이 정해진 뒤에 쓸 수 있다)
3. **첫 응답은 즉시** 보냅니다 (콜백 사용, 5초 안). 이후 작업은 콜백 경로에서 수행합니다.
4. `transcribe_call(audio, filename)`. 걸린 시간(`elapsed`)을 잽니다.
5. `analyze_call(transcript, time_budget_s = 40 - elapsed)`. PR #105 는 다운로드부터 카드 렌더링까지를 `asyncio.wait_for(..., CALLBACK_DEADLINE_SECONDS=45)` 로 감쌉니다. 다운로드(한도 15초)·STT·분리도 이 45초 안에 들어가므로, 분석 예산은 v3 B3 대로 `CALLBACK_DEADLINE_SECONDS - 5`(40초)에서 `elapsed` 를 뺍니다. `45 - elapsed` 로 주면 분석이 45초를 다 써서 카드 렌더링 전에 바깥 `wait_for` 가 작업을 취소하고 결과가 시간 초과(R4)로 바뀝니다. `elapsed` 는 다운로드 시작부터 잽니다. 남은 예산이 너무 적으면(예: 5초 미만) 호출하지 않고 대본 없음/미확인 카드로 처리합니다.
6. 결과를 **먼저 저장** (콜백은 1회성) -> 콜백 카드 전송. 콜백이 만료·실패하면 `결과 확인` 조회가 저장본을 보여줍니다 (처리 중이면 K1, 실패면 R4, 없으면 K4).
7. **결과 카드 버튼은 `자세히 보기`·`다른 녹음 확인하기`입니다(이슈 #115, 2026-10-29로 D15 기준에서 다시 바뀜).** `112 신고`는 V1에 달지 않습니다 — 신고·상담 버튼은 결과 카드가 아니라 Q1 → K6 로 안내합니다. `v1-voice-result.json`(`frontend/kakao-templates/`, FE `refactor/voice-phishing-fe` 소유)이 두 버튼을 고정값으로 담고 있어 BE가 버튼을 조립하지 않습니다. 옛 목업 카드 `r-voice-result.json` 은 지워졌습니다(PR #105 당시 썼던 것).

한 사용자가 분석 중에 또 보내면 K5 (문자와 같은 잠금 규칙).

## 기동 시

`backend/src/server/lifespan.py` 에서 `warm_up_kb()` 옆에 `warm_up_voice_case_search()` 를 부릅니다. 콜드 적재가 약 1.4초라 1초 검색 예산을 넘기기 때문입니다(같은 호출이 사례 첨부 관문 모델도 미리 읽습니다. 약 30ms, 관문 모델 적재 실패는 로그만 남기고 기동은 계속하며 그 경우 사례는 붙지 않고 `similar_cases` 는 미확인이 됩니다). 실패해도 서버는 떠야 하고(기존 `warm_up_kb` 와 같은 처리), 부르지 않으면 첫 통화 요청이 적재 비용을 떠안아 `similar_cases:timeout` 이 됩니다.

## 환경 변수

엘리스 ML API 는 모델마다 Base URL 이 다릅니다.

| 이름 | 용도 | 기본값 |
| --- | --- | --- |
| `STT_BASE_URL` | whisper 엔드포인트 루트. **`/v1` 없이** 넣습니다(코드가 `/v1/audio/transcriptions` 를 붙임). 예: `https://mlapi.run/<엔드포인트 ID>` | 필수 |
| `STT_MODEL` | STT 모델 | `whisper-large-v3` |
| `DIARIZE_BASE_URL` | 화자 분리용 Claude 엔드포인트 루트. **`/v1` 없이** 넣습니다(코드가 `/v1/messages` 를 붙임) | 필수 |
| `DIARIZE_MODEL` | 화자 분리 모델 | `claude-sonnet-5-5` |
| `LLM_API_KEY` | 위 호출 공용 키 | 필수 |
| `LLM_BASE_URL` / `LLM_MODEL` / `CLAIMS_MODEL` | 주장 읽기 (기존, 읽기만). `LLM_BASE_URL` 은 OpenAI 클라이언트용이라 지금 Render 값처럼 **`/v1` 까지** 넣습니다. 위 두 변수와 형식이 다르니 값을 복사하지 마세요(`…/v1/v1/messages` 404). `CLAIMS_MODEL` 이 없으면 `LLM_MODEL` 을 씁니다 | 기존 값 |

Render 환경 변수와 `.env.example` 에 추가해야 합니다.

## 결과 -> 카드 매핑

| `VoiceCaseResult` | 카드 |
| --- | --- |
| `conclusion` = `not_analyzable` | **`ve3-unrecognized.json`**(받아쓰기 실패·빈 대본, 고정 문구, 플레이스홀더 없음) |
| `conclusion` = `input_required` | **`ve4-no-claim.json`**(사칭·요구를 못 찾음, 고정 문구). AI `decide_call()`(`ai/src/ai/case/gate.py`)이 이 분기에서 `questions` 를 채우지 않아 Q1 이 구조적으로 안 붙는다 |
| `conclusion` = `suspected` / `inconclusive`(그 밖, `official_domain` 은 통화에 안 나옴) | `v1-voice-result.json`. 제목만 3갈래(`suspected` → `⚠️ 보이스피싱이 의심돼요`, `inconclusive`+`caution` → `🔎 주의가 필요한 통화예요`, 그 밖 → `🤔 판단하기 어려워요`) |
| `caution` | 주의 문구 덧붙임 여부 + `call_claims` 칸 제목(`주의할 점`/`통화에서 들은 내용`) |
| `stop_actions` | 지금 멈출 행동 목록(`stop_line`). V1은 `hang_up` 포함, A2(`stop_line`)는 `hang_up` 제외 |
| `claims` (`identity`/`demands`/`pretexts`/`blockers`) | V1 `call_claims` 4줄: 기관=`identity[].quote`(여러 개면 전부), 상대가 한 말=**첫 `demands[].quote`**, 내세운 사정=`blockers`(있으면) 또는 `pretexts` 중 첫 번째, 요구한 것=`demands[].kind` 라벨 최대 3개. 전부 비면 칸 제목까지 빈 문자열. **"내세운 사정"에 `blockers`(원래 "하지 말라"는 통제성 발화)가 들어가는 게 맞는지는 미확인 — 아래 "FE/PM 확인 필요" 참고** |
| `number_checks` | `checked`/`unchecked` 줄로 들어간다(일반 `phone_number` 줄 **대신**). `official`/`not_official`→확인한 것(`상대가 말한 번호({number}): {org} 대표번호와 같아요/아니에요`), `org_not_listed`→확인하지 못한 것(`상대가 말한 번호가 {org} 번호인지 (대표번호 자료에 없는 기관이에요)`, "공식이 아니다"가 아님) |
| `official_contact` (`org`, `number`, `source_url`) | V1 `verify_line`: `{org} 대표번호({number})로 직접 전화해 확인하세요.` 없으면(`null`) `상대가 말한 기관의 대표번호를 직접 찾아 전화해 확인하세요.` 번호는 대표번호 KB 에서만 오며 녹음 속 번호가 아님. `source_url`은 카드에 쓰지 않음 |
| `response_actions` | K6 대응 줄. 분석 직후에는 비어 있고 `apply_call_answers` 가 채움. 비어 있으면 `a2-not-yet-voice.json` |
| `checked` / `unchecked` (+`reason`) | 확인한 것 / 확인하지 못한 것. 둘을 섞지 않음 (`not_run` 과 `failed` 구분). `phone_number` 라벨은 `상대가 말한 전화번호`(둘 다 통일), `transcript_accuracy`는 reason 무관하게 늘 `받아 적은 글이 정확한지 (말을 글로 옮겨 틀릴 수 있어요)` |
| `failures` | 실패 이유 문구 (`timeout`, `llm_error`, `invalid_output` 등). 화자 분리 실패는 여기가 아니라 `unchecked` 와 `inconclusive`·`caution` 으로 나타남 |
| `similar_cases` | **FE 결정(이슈 #115, 2026-10-29)으로 V1·자세히 보기 어디에도 쓰지 않는다.** `VoiceCaseResult.similar_cases` 데이터 자체는 그대로 저장·복원된다 — 카드 렌더러가 이 필드를 안 읽을 뿐이다(`악성 확률로 보일 수 있다는 FE 우려`) |
| `explanation` | 부가 설명. 카드에 아직 안 씀(비어 있어도 카드는 나가야 함 조건은 만족) |
| `questions[0]` (Q1, `option_ids`: `entered_info`, `installed_app`, `sent_money`, `not_yet`) | 결과 아래 `이미 하신 게 있나요?` 리스트 카드(`q1-already-acted.json`). 라벨은 FE. 블록은 **`block_answer`=`6ae336cd25d4bc48e2bb8f2a`**(2026-10-29 관리자센터 확정) — "자세히 보기" 블록(`6aad3abfe745d3358bd90c8d`)과 다르다, 재사용 금지 |

Q1 답은 쌓입니다(`job["qa_answers"]`). 답이 들어올 때마다 저장된 결과와 지금까지의 답으로 `apply_call_answers(result, answers)` 를 불러 다시 저장하고, `response_actions` 로 K6 카드를 만듭니다(비어 있으면 A2). 순서는 함수가 정한 순서를 그대로 씁니다.

대응 문장(`response_actions` → 문장)은 AI 쪽이 정하고 BE는 그대로 번호만 붙입니다(`call_1332` 는 없음, #107에서 이미 뺌).

| Q1 답 | `response_actions` |
| --- | --- |
| `sent_money` (상품권 요구 있음) | `contact_gift_card_issuer`, `report_112` |
| `sent_money` (그 밖) | `request_payment_stop`, `report_112` |
| `entered_info` ("주민" 요구 있음) | `register_info_exposure` |
| `entered_info` (그 밖) | `change_password`, `register_info_exposure` |
| `installed_app` | `request_payment_stop`, `isolate_phone`, `use_other_device`, `remove_app_and_check_device` |

**K6 버튼 규칙은 문자와 녹음이 다릅니다(`k6-response.json` 은 공용 템플릿, 버튼 조립 로직만 시나리오별).**

- **문자(D17, 기존 그대로 유지)**: `report_112` 에만 `112 신고`, 버튼 달 대응이 없으면 `KISA 상담하기`, `installed_app` 만 고른 경우는 버튼 없음.
- **녹음(D18, 2026-10-29 신규, `render_voice_answer_card()`)**: 버튼은 `response_actions` 코드가 아니라 **쌓인 Q1 답(answers) 자체**를 기준으로 고른다.

  | 쌓인 Q1 답 | 버튼 | `contact_line` |
  | --- | --- | --- |
  | `sent_money` 또는 `entered_info` 가 있음 | `112 신고`(phone 112) · `금감원 상담`(phone 1332) | `신고·상담: 112(경찰), 1332(금융감독원)` (phone 버튼이 PC 카톡에 안 보여서 본문에도 적음) |
  | `installed_app` 만 있음 | 없음(빈 목록) | 빈 문자열 |
  | 그 밖(버튼 달 대응이 없음) | `1394 상담`(phone 1394) | 빈 문자열 |

  `contact_line` 이 비면 템플릿이 남기는 빈 줄을 BE가 정리합니다. **"`installed_app`과 `not_yet`을 함께 고른 경우"는 지금 구현에서 "`installed_app` 만"에 안 들어가 세 번째 행(1394)으로 빠집니다 — 의도한 동작인지 미확인**(아래 "FE/PM 확인 필요" 참고).

## 개인정보

- 분석 경로의 `segments` 는 정규식으로 가려진 상태입니다 (`mask_pii`). 기관명·전화번호는 근거로 써야 해서 남깁니다.
- 대본을 **저장하거나 외부로 보내기 전**에는 주입형 `EliceMasker` (`ai/src/ai/case/privacy.py`)로 한 번 더 가립니다. 엔드포인트 주소는 아직 미정이라 `base_url`/`api_key`/`timeout` 을 설정으로 받게 하고, 미정이면 대본은 저장하지 않고 결과(`VoiceCaseResult`)만 저장합니다.
- 녹음 원본은 분석 직후 폐기합니다 (저장 금지). 녹음 전용 전송 동의(W3v)는 FE 초안 VF4 를 따릅니다.

## 알려진 한계 / 후속

- 일부 오디오 바이트가 Cloudflare 403 으로 막힙니다. voice-stt 참고 구현은 gain 0.99 -> 0.97 로 바꿔 재시도합니다. 디코딩이 필요해서 BE 쪽 일입니다 (AI는 바이트만 받음).
- `audio_seconds` 는 wav 만 채워집니다. 그 외 형식은 `None` 입니다.
- 지식베이스 대본의 라이선스 문제: 금감원 자료는 "무단 복제·배포 금지"입니다. 공개 저장소에 올리기 전에 해결해야 합니다.
- 가족·지인 사칭 유형은 사례 검색이 약합니다 (결과가 `inconclusive` 로 나올 수 있음). 사례 첨부 관문 때문에 `similar_cases` 가 빈 채로 나가는 것은 오류가 아닙니다.
- 5초 안에 카카오 첫 응답, STT 실측은 3~4분 녹음에 12~16초입니다. 더 긴 녹음의 한도는 정해지지 않았습니다.

## 완료 조건

(2026-10-30 갱신 — 체크된 항목은 `feature/voice-fe-alignment-be`/`refactor/voice-phishing-be` 에서 구현·테스트까지 확인됨. 미체크는 아직 코드가 없거나 결정이 안 남)

- [x] 녹음 URL 검증(호스트)과 내려받기 — 음성메시지(`media.type=="audio"`)·파일 첨부(utterance) 두 경로 모두 구현
- [ ] **녹음 크기·형식·길이 검증과 초과·불가 시 전용 안내 카드(VE1·VE2)** — 크기 상한(25MB)만 있고 전용 카드 분기는 아직 없음(다른 담당자 작업 중, `main.py` 미착수). 길이 상한 자체가 미정
- [x] `mock_analyze_voice_case()` 를 `transcribe_call`/`analyze_call` 로 교체, `render_voice_result_card()` 를 `VoiceCaseResult` 기준(conclusion별 V1/VE3/VE4 분기 포함)으로 재작성
- [x] 첫 응답 즉시, 이후 `transcribe_call` -> `analyze_call(time_budget_s)` 를 콜백 경로에서 실행. 예산은 `CALLBACK_DEADLINE_SECONDS(45) - 5` 에서 다운로드+STT+화자분리 경과 시간을 뺀 값, 5초 미만이면 `analyze_call` 에 0초를 줘 실제 분석을 사실상 건너뜀
- [x] 결과를 콜백 전에 저장하고, `결과 확인` 이 저장본(처리 중 K1 / 실패 R4 / 없음 K4)을 보여줌
- [x] V1/VE3/VE4 결과 카드와 Q1 리스트 카드 렌더링, Q1 답은 `apply_call_answers` 결과로 K6(D18)/A2 생성, 전용 블록(`block_answer`) 사용
- [x] V1 버튼 `자세히 보기`·`다른 녹음 확인하기`(D15 재변경), K6 버튼은 녹음 전용 D18 규칙(문자 D17은 그대로 유지)
- [x] lifespan 에서 `warm_up_voice_case_search()` 호출 (실패해도 기동) — 테스트로 확인됨
- [x] 환경 변수 6개를 `.env.example` 에 반영. **Render 실제 설정은 별도 확인 필요**(`STT_BASE_URL`/`DIARIZE_BASE_URL` 미설정이면 전부 `ve3-unrecognized`(알아듣지 못함)로만 응답)
- [x] 대본 저장·외부 전송 전 마스킹 — 엔드포인트 미정이라 대본 자체를 저장하지 않는 방식으로 조건 충족(`EliceMasker` 호출은 아직 안 함)
- [x] STT 실패(`not_analyzable`), 화자 분리 실패(`TranscriptResult.speaker_split_failed`), 시간 초과 경로 테스트
- [x] 한 사용자 동시 요청 K5 잠금
- [x] 의존 방향 유지 (`backend` -> `ai`), `ai/` 는 카카오 payload 를 모름
- [x] 관련 테스트와 전체 테스트 통과(음성 51개 + 문자 카드 렌더링 75개, 전체 스위트는 이 작업과 무관한 사전 존재 실패 3~5개만 남음)
- [ ] VE1/VE2 구현 후 관련 테스트 추가(다른 담당자)
- [ ] 이 문서의 "FE/PM 확인 필요" 항목 결론 반영

## FE/PM 확인 필요 (2026-10-30 조사, 임의로 정하지 않음)

| 항목 | 지금 코드/문서 상태 | 확인할 것 |
| --- | --- | --- |
| `call_claims` 의 "내세운 사정 = `blockers` → `pretexts`" | "blockers 있으면 그걸, 없으면 pretexts" 로 해석해 구현(`_voice_call_claims`). `blockers`는 원래 "다른 사람에게 말하지 마세요" 같은 통제성 발화라 "내세운 사정"(사유)으로 보여 주는 게 의미상 맞는지 의문 | 이슈 원문 "blockers → pretexts" 가 우선순위(fallback) 뜻이 맞는지, `blockers` 를 "사정" 칸에 넣는 게 의도인지 |
| K6 D18 "버튼 달 대응이 없음 → 1394" 조건 | `installed_app`+`not_yet` 조합처럼 `installed_app` 외에 무해한 답(`not_yet`)이 섞이면 "installed_app만"이 아니게 돼 1394 버튼이 붙음 — `response_actions_for()` 가 만들 수 있는 조합상 이 세 번째 행이 실제로 트리거되는 다른 입력이 있는지도 불분명 | "installed_app만"의 기준이 답 집합 전체인지, 응답에 영향 주는 답만인지 |
| `.webm` 지원 여부 | 코드(`main.py` `_VOICE_FILE_EXTENSIONS`/`_VOICE_ATTACHMENT_EXTENSIONS`)는 m4a·mp3·wav·webm, FE 카드(`v0-voice-check.json`)는 m4a·mp3·wav만 안내 | 실제 카카오 녹음이 webm으로 올 수 있는지, 디코더(STT)가 webm을 받는지 확인 전까지 변경 금지(이번 작업·VE1/VE2 작업 모두 해당) |
| 녹음 길이 상한 | 미정. VE2 `reason_line`에 "N분보다 길어요" 문구가 필요하지만 N을 못 정함(`voice-phishing-flow.md` Q-7) | 길이 상한 값(분 단위), 초과 판정을 다운로드 전(헤더)·후(디코딩) 중 어디서 할지 |
| STT 서비스명 / W3v 배포 | `w3v-voice-consent.json` 의 `{{ external_service }}` 가 비어 있고, `block_voice_consent` 블록 ID는 이미 확정됨(`6ae335a8b074221a79bea79f`). 서비스명이 안 정해져 **W3v는 배포 보류**(이슈·README 모두 명시) | STT 서비스(엘리스 ML API 뒤의 실제 모델/제공자) 사용자 노출용 이름, 확정 후 W3v 배포 가능 여부 |

이 표의 어느 항목도 이번 작업에서 임의로 결정하지 않았다 — 결론이 나기 전까지 관련 코드(`.webm` 목록, K6 세 번째 행, `blockers` 처리)는 지금 동작을 유지한다.
