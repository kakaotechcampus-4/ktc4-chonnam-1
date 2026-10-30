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
   - **VE1/VE2/R4 입력 검증 구현 완료**(`a6c5b77`). `backend/src/server/voice_input.py::inspect_voice_audio()`는 MIME·확장자만 신뢰하지 않고 바이트/컨테이너의 오디오 단서를 검사한다. 비오디오 → VE1, 미지원 오디오·빈 파일·손상된 WAV·25MB 초과 → 원인별 `reason_line`을 채운 VE2, 다운로드 실패·시간 초과 → R4. 정확히 25×1024×1024바이트인 WAV의 STT 전달과 1바이트 초과 차단도 테스트했다.
   - **길이 상한은 미정**이며 `MAX_VOICE_DURATION_SECONDS=None`이다. WAV 측정과 가상 상한의 경계/초과 처리만 구현했다. 상한을 설정하고 압축 형식의 길이를 측정하지 못하면 VE2로 끝내며 통과로 간주하지 않는다. 전체 형식 길이 측정 도구/실행 환경은 추가 결정이 필요하다.
   - MPEG 프레임·ISO-BMFF 오디오 handler·WebM 오디오 CodecID 확인은 **압축 오디오 전체 디코딩/모든 손상 검증을 완료했다는 뜻이 아니다.** M4A/WebM 테스트에는 메타데이터 fixture를 쓰며 실제 STT/코덱 검증은 별도다. 원본 파일은 바이트로 읽은 즉시 지운다.
3. **첫 응답은 즉시** 보냅니다 (콜백 사용, 5초 안). 이후 작업은 콜백 경로에서 수행합니다.
4. `transcribe_call(audio, filename)`. 걸린 시간(`elapsed`)을 잽니다.
5. `analyze_call(transcript, time_budget_s = 40 - elapsed)`. PR #105 는 다운로드부터 카드 렌더링까지를 `asyncio.wait_for(..., CALLBACK_DEADLINE_SECONDS=45)` 로 감쌉니다. 다운로드(한도 15초)·STT·분리도 이 45초 안에 들어가므로, 분석 예산은 v3 B3 대로 `CALLBACK_DEADLINE_SECONDS - 5`(40초)에서 `elapsed` 를 뺍니다. `45 - elapsed` 로 주면 분석이 45초를 다 써서 카드 렌더링 전에 바깥 `wait_for` 가 작업을 취소하고 결과가 시간 초과(R4)로 바뀝니다. `elapsed` 는 다운로드 시작부터 잽니다. 남은 예산이 5초 미만이면 함수를 생략하는 대신 `analyze_call(..., time_budget_s=0.0)`으로 호출해 읽기·검색 LLM 작업을 사실상 건너뛰고 미확인 결과를 받습니다.
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
| 입력 내용이 비오디오 | `ve1-not-audio.json`, STT 호출 안 함 |
| 미지원 오디오·빈 파일·손상된 WAV·크기 초과 | `ve2-invalid-audio.json`, 원인별 `reason_line`, STT 호출 안 함 |
| 다운로드 실패·시간 초과 | 공용 `r4-unavailable.json`, 동기·콜백 경로 모두 |
| `conclusion` = `not_analyzable` | **`ve3-unrecognized.json`**(받아쓰기 실패·빈 대본, 고정 문구, 플레이스홀더 없음) |
| `conclusion` = `input_required` | **`ve4-no-claim.json`**(사칭·요구를 못 찾음, 고정 문구). AI `decide_call()`(`ai/src/ai/case/gate.py`)이 이 분기에서 `questions` 를 채우지 않아 Q1 이 구조적으로 안 붙는다 |
| `conclusion` = `suspected` / `inconclusive`(그 밖, `official_domain` 은 통화에 안 나옴) | `v1-voice-result.json`. 제목만 3갈래(`suspected` → `⚠️ 보이스피싱이 의심돼요`, `inconclusive`+`caution` → `🔎 주의가 필요한 통화예요`, 그 밖 → `🤔 판단하기 어려워요`) |
| `caution` | 주의 문구 덧붙임 여부 + `call_claims` 칸 제목(`주의할 점`/`통화에서 들은 내용`) |
| `stop_actions` | 지금 멈출 행동 목록(`stop_line`). V1은 `hang_up` 포함, A2(`stop_line`)는 `hang_up` 제외 |
| `claims` (`identity`/`demands`/`pretexts`/`blockers`) | V1 `call_claims` 4줄: 기관=`identity[].quote`(여러 개면 전부), 상대가 한 말=**첫 `demands[].quote`**, 내세운 사정=`blockers`(있으면) 또는 `pretexts` 중 첫 번째, 요구한 것=`demands[].kind` 라벨 최대 3개. 전부 비면 칸 제목까지 빈 문자열. **blockers는 원래 연락처로 확인하지 못하게 하는 사정이다. pretexts와 함께 있을 때 fallback인지 함께 표시할지는 아래 FE/AI/PM 확인 필요** |
| `number_checks` | `checked`/`unchecked` 줄로 들어간다(일반 `phone_number` 줄 **대신**). `official`/`not_official`→확인한 것(`상대가 말한 번호({number}): {org} 대표번호와 같아요/아니에요`), `org_not_listed`→확인하지 못한 것(`상대가 말한 번호가 {org} 번호인지 (대표번호 자료에 없는 기관이에요)`, "공식이 아니다"가 아님) |
| `official_contact` (`org`, `number`, `source_url`) | V1 `verify_line`: `{org} 대표번호({number})로 직접 전화해 확인하세요.` 없으면(`null`) `상대가 말한 기관의 대표번호를 직접 찾아 전화해 확인하세요.` 번호는 대표번호 KB 에서만 오며 녹음 속 번호가 아님. `source_url`은 카드에 쓰지 않음 |
| `response_actions` | K6 대응 줄. 분석 직후에는 비어 있고 `apply_call_answers` 가 채움. 비어 있으면 `a2-not-yet-voice.json` |
| `checked` / `unchecked` (+`reason`) | 확인한 것 / 확인하지 못한 것. 둘을 섞지 않음 (`not_run` 과 `failed` 구분). `phone_number` 라벨은 `상대가 말한 전화번호`(둘 다 통일), `transcript_accuracy`는 reason 무관하게 늘 `받아 적은 글이 정확한지 (말을 글로 옮겨 틀릴 수 있어요)` |
| `failures` | 실패 이유 문구 (`timeout`, `llm_error`, `invalid_output` 등). 화자 분리 실패는 여기가 아니라 `unchecked` 와 `inconclusive`·`caution` 으로 나타남 |
| `similar_cases` | 최신 FE 댓글(2026-10-30)에 따라 V1 확인 상태를 복원한다. completed+matches면 확인, matches 없음/kb_empty면 비교할 사례 없음, unchecked failed/timeout이면 검색 문제로 구분한다. 실제 matches가 있을 때만 캐러셀 두 번째 장을 붙인다. 현재 AI에 type이 없어 수법명을 추측하지 않고 일반적인 사례 발견 안내만 한다(#109 의존) |
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

**K6 버튼은 D17을 따른다(2026-10-30 최신 FE 요구). D18은 철회됐다.**

문자는 기존 D17 구현을 유지한다. 녹음은 누적 Q1 답에서 아래 순서로 선택한다.

| 누적 행동 | 버튼 | contact_line |
| --- | --- | --- |
| sent_money 포함(정보·앱·not_yet 포함 여부와 무관) | 112 신고 | 빈 문자열 |
| 돈 없이 entered_info 포함 | 1394 상담 | 상담: 피싱 통합 상담 1394 (24시간) |
| 돈·정보 없이 installed_app 포함 | 없음 | 빈 문자열 |
| 그 밖의 K6 또는 A2 | 1394 상담 | K6에서만 위 상담 줄, A2는 고정 템플릿 |

D16의 답 누적 계약은 유지한다. not_yet은 이전 행동을 철회하지 않으며,
앱 설치+not_yet도 앱 설치 대응만 남으므로 전화 버튼이 없다.
정보+돈은 112만 표시한다. B2d는 상황이 미확정인 진입 카드이므로 FE 템플릿대로 112·1394를 함께 안내한다.
1332 대응 버튼은 제거했다. 공식 번호 KB에서 반환한 official_contact의 사실 정보는 바꾸지 않는다.

## 2026-10-30 FE 계약 동기화

기준: `origin/refactor/voice-phishing-fe`의 `1c226c9`,
[이슈 #115 최신 FE 댓글](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/issues/115#issuecomment-6514684572).
B2d, VE4, C2 제목, A2 녹음, 녹음 캐러셀과 README·설계 문서를 동기화했다.
K6 JSON은 이미 FE와 같으며 BE의 버튼·contact_line 조합만 변경했다.
관련 없는 문자 카드·Q2 신규 구현은 가져오지 않았다.

- 사례 type은 `CaseMatch`에 없다. case_id/categories/matched_variant에서 유형을 추측하지 않는다.
  현재는 “이번 통화와 비슷한 예시 통화를 찾았어요.”와 판정에 쓰지 않았다는 안내만 표시한다.
  FE의 유형별 similar_type/similar_line은 #109 AI 인터페이스 확정 후 연결해야 한다.
- claim_note는 기본 “통화 상대가 한 말만 근거로 썼어요.”다.
  #116 문서의 명시적 unchecked.item=speaker가 전달되는 경우에만 중립 말머리와
  “누가 한 말인지 가리지 못해 통화에 나온 말을 옮겼어요.”를 표시하도록 준비했다.
  caution으로 화자 상태를 추측하지 않는다. AI 모델에는 아직 이 항목이 없으므로
  실제 화자 미확인 전달·한 화자 판별·번호 검증 변경 등 #116 자체 구현은 남아 있다.
- blockers/pretexts의 기존 첫 항목 fallback 매핑은 변경하지 않았다. FE의 연결 표시 요구와
  AI의 두 필드 의미·표시 범위는 협의가 필요하다.
- #119 링크 추출 전환과 STT·배포 설정은 변경하지 않았다.
- FE 최신 README에는 문자 A2/A3/R6b/Q2 계약도 추가되어 있다. 이번 음성 범위에서는
  해당 문자 템플릿·새 분기 구현을 동기화하지 않았다. 공유 C2 제목만 최신으로 맞추고
  기존 문자 판정·카드 동작은 회귀 테스트로 보존했다.

### 최신 FE 반영 후 검증 기록

위 Worktree 전용 Python 환경에서 실행했다. 모든 외부 STT/LLM은 기존 mock을 사용했다.

| pytest 대상 (`-B -X utf8 -m pytest -p no:cacheprovider`, `-q --tb=short`) | 결과 | 실행 시간 |
| --- | --- | --- |
| `tests/test_voice_message_flow.py tests/test_result_card_renderer.py tests/test_detail_carousel.py` | 164 passed | 9.90초 |
| `tests` | 645 passed, 2 skipped | 27.68초 |
| `tests ai/tests` | 1937 passed, 3 skipped | 49.65초 |

기존 입력 검증·문자·Q1 누적 동작을 유지하면서 D17 우선순위, contact_line,
사례 발견/빈 검색/검색 오류/시간 초과, 유형 추측 금지, claim_note의 명시적 표시 조건을 검증했다.
아래의 1926개 실행 기록은 최신 FE 반영 전 통합 기준에 대한 이력이다.

AI HTML 예산 실패는 이번 전체 실행에서도 재현되지 않았다.
`test_partial_html_preserves_observed_form_without_claiming_completion[element-limit]`의
JUnit 테스트 총시간은 0.025초, `text-limit`은 0.005초였다(HTML 검사 단독 측정이 아님).
XML은 Git에서 제외된 `.venv/voice-115-fe-alignment-results.xml`에만 생성했다.
실제 녹음/STT·실기기·배포 검증을 수행한 것은 아니며, 100ms 예산도 변경하지 않았다.

## 개인정보

- 분석 경로의 `segments` 는 정규식으로 가려진 상태입니다 (`mask_pii`). 기관명·전화번호는 근거로 써야 해서 남깁니다.
- 대본을 **저장하거나 외부로 보내기 전**에는 주입형 `EliceMasker` (`ai/src/ai/case/privacy.py`)로 한 번 더 가립니다. 엔드포인트 주소는 아직 미정이라 `base_url`/`api_key`/`timeout` 을 설정으로 받게 하고, 미정이면 대본은 저장하지 않고 결과(`VoiceCaseResult`)만 저장합니다.
- 내려받은 임시 파일은 바이트로 읽은 즉시 삭제하며 원본 파일을 영속 저장하지 않습니다. 바이트는 STT 호출에 사용합니다. 외부 STT 서비스의 보관 정책 검증과 녹음 전용 전송 동의(W3v)는 별도 사항입니다.

## 알려진 한계 / 후속

- 일부 오디오 바이트가 Cloudflare 403 으로 막힙니다. voice-stt 참고 구현은 gain 0.99 -> 0.97 로 바꿔 재시도합니다. 디코딩이 필요해서 BE 쪽 일입니다 (AI는 바이트만 받음).
- `audio_seconds` 는 wav 만 채워집니다. 그 외 형식은 `None` 입니다.
- 지식베이스 대본의 라이선스 문제: 금감원 자료는 "무단 복제·배포 금지"입니다. 공개 저장소에 올리기 전에 해결해야 합니다.
- 가족·지인 사칭 유형은 사례 검색이 약합니다 (결과가 `inconclusive` 로 나올 수 있음). 사례 첨부 관문 때문에 `similar_cases` 가 빈 채로 나가는 것은 오류가 아닙니다.
- 5초 안에 카카오 첫 응답, STT 실측은 3~4분 녹음에 12~16초입니다. 더 긴 녹음의 한도는 정해지지 않았습니다.

## 완료 조건

(2026-10-30 갱신 — 기준 `2349b18`, 입력 검증 `a6c5b77`, 문서 `178c36f`를 `feature/voice-115-integration`에서 통합 검증. 체크는 해당 구현 범위만 뜻하며 실서비스 배포·전체 코덱 검증을 뜻하지 않는다.)

- [x] 녹음 URL 검증(호스트)과 내려받기 — 음성메시지(`media.type=="audio"`)·파일 첨부(utterance) 두 경로 모두 구현
- [x] **바이트/헤더 기반 입력 검증과 VE1/VE2/R4 분기** — 25MB 경계·비오디오·미지원 형식·빈 파일·손상된 WAV·다운로드 실패·시간 초과. 원인별 VE2 문구와 파일 정리 유지
- [ ] **운영 녹음 길이 상한 확정·전 형식 길이 측정·압축 오디오 전체 디코딩 검증** — WAV 측정만 가능, 운영 상한 미설정. 새 라이브러리/ffprobe/배포 설정을 추가하지 않음
- [x] `mock_analyze_voice_case()` 를 `transcribe_call`/`analyze_call` 로 교체, `render_voice_result_card()` 를 `VoiceCaseResult` 기준(conclusion별 V1/VE3/VE4 분기 포함)으로 재작성
- [x] 첫 응답 즉시, 이후 `transcribe_call` -> `analyze_call(time_budget_s)` 를 콜백 경로에서 실행. 예산은 `CALLBACK_DEADLINE_SECONDS(45) - 5` 에서 다운로드+STT+화자분리 경과 시간을 뺀 값, 5초 미만이면 `analyze_call` 에 0초를 줘 실제 분석을 사실상 건너뜀
- [x] 결과를 콜백 전에 저장하고, `결과 확인` 이 저장본(처리 중 K1 / 실패 R4 / 없음 K4)을 보여줌
- [x] V1/VE3/VE4 결과 카드와 Q1 리스트 카드 렌더링, Q1 답은 `apply_call_answers` 결과로 K6(D17)/A2 생성, 전용 블록(`block_answer`) 사용
- [x] V1 버튼 `자세히 보기`·`다른 녹음 확인하기`(D15 재변경), K6 버튼은 최신 D17 규칙(문자 분석은 그대로 유지)
- [x] lifespan 에서 `warm_up_voice_case_search()` 호출 (실패해도 기동) — 테스트로 확인됨
- [x] 환경 변수 6개를 `.env.example` 에 반영. **Render 실제 설정은 별도 확인 필요**(`STT_BASE_URL`/`DIARIZE_BASE_URL` 미설정이면 전부 `ve3-unrecognized`(알아듣지 못함)로만 응답)
- [x] 대본 저장·외부 전송 전 마스킹 — 엔드포인트 미정이라 대본 자체를 저장하지 않는 방식으로 조건 충족(`EliceMasker` 호출은 아직 안 함)
- [x] STT 실패(`not_analyzable`), 화자 분리 실패(`TranscriptResult.speaker_split_failed`), 시간 초과 경로 테스트
- [x] 한 사용자 동시 요청 K5 잠금
- [x] 의존 방향 유지 (`backend` -> `ai`), `ai/` 는 카카오 payload 를 모름
- [x] VE1/VE2/R4 테스트 38개 추가(`TestVoiceInputValidation`). 기존 V1/VE3/VE4·Q1/K6/A2·문자 테스트를 함께 실행하며 통합 결과는 아래 실제 실행 기록으로 구분
- [x] K6 D17 누적 답 우선순위·중립 답 역순·정보+돈 조합을 회귀 테스트로 검증
- [ ] 이 문서의 "FE/PM 확인 필요" 항목 결론 반영

## 통합 테스트 실행 기록 (2026-10-30)

Worktree `ktc4-voice-input-validation`, 브랜치 `feature/voice-115-integration`.
기준 `2349b18` 위 구현 `a6c5b77`과 문서 `178c36f`를 통합했다(문서 cherry-pick 결과 `2a0c5e0`).
이 Worktree 전용 `.venv`의 Python 3.14.5와 설치된 테스트 의존성을 사용했다.
외부 STT/LLM·실서비스 배포는 실행하지 않았으며 테스트 기대값/시간 예산도 완화하지 않았다.

| 명령 (`.venv/Scripts/python.exe -B -X utf8 -m pytest -p no:cacheprovider` 뒤 인자) | 결과 | 전체 실행 시간 |
| --- | --- | --- |
| `tests/test_voice_message_flow.py tests/test_result_card_renderer.py tests/test_message_facts_card.py tests/test_media_guard.py -q --tb=short` | 181 passed | 9.62초 |
| `tests -q --tb=short` | 634 passed, 2 skipped | 27.50초 |
| `tests ai/tests -q --tb=short --junitxml=.venv/voice-115-integration-results.xml` | 1926 passed, 3 skipped | 49.91초 |

기존 Starlette TestClient 사용 중단 경고와 pytest-asyncio fixture loop scope 미설정 경고는 남아 있다.
결과 XML은 Git에서 제외된 이 Worktree의 `.venv` 안에만 생성했다.

과거 입력 검증 작업에서 전체 실행 중 실패했던 AI 사례는 다음과 같다.

`ai/tests/test_revision_integration.py::test_partial_html_preserves_observed_form_without_claiming_completion[element-limit]`

`ai/page.py`의 HTML 검사 예산은 0.100초다. 이번 통합 전체 실행에서는 **실패 재현 안 됨**:
JUnit 테스트케이스 총시간(설정·정리 포함)은 `element-limit` 0.024초, `text-limit` 0.004초였다.
이 시간은 HTML 파서만의 별도 벤치마크가 아니다. 이전의 전체 실행 실패/분리 실행 통과 이력은
환경·실행 순서에 민감할 가능성을 남기며, 이번 한 번의 통과를 해당 문제 해결로 선언하지 않는다.
AI 구현·테스트·100ms 예산은 변경하지 않았다.

## FE/AI/PM 확인 필요 (2026-10-30 조사, 임의로 정하지 않음)

| 항목 | 지금 코드/문서 상태 | 확인할 것 |
| --- | --- | --- |
| `call_claims` 의 "내세운 사정 = `blockers` → `pretexts`" | 실제 AI 프롬프트(`ai/src/ai/prompts/v1/claims_call.md`)상 `pretexts`는 내세운 명분·사정, `blockers`는 원래 연락처로 확인하지 못하게 하는 사정(폰 고장/통화 불가)이다. 단순한 "말하지 말라" 일반 통제 발화로 정의하면 부정확하다. 두 모델은 각각 인용을 담고 `verify.py`가 대본에 존재하는 인용을 보존하며 blockers는 `CONTACT_BLOCKER` 근거도 생성한다. 현재 BE는 blockers의 첫 항목, 없으면 pretexts의 첫 항목을 표시한다 | FE copy는 "blockers 먼저, 이어서 pretexts", 이슈는 화살표 표기라 fallback/연결 여부가 불명확하다. FE/AI/PM이 둘 다 있을 때 표시 범위·순서·라벨을 확정해야 한다. 현재 매핑 변경 없음 |
| `.webm` 지원 여부 | 파일 첨부 인식은 `main.py::_VOICE_ATTACHMENT_EXTENSIONS`, 내용 검사는 `backend/src/server/voice_input.py`의 WebM 오디오 CodecID 검사로 유지한다. FE 카드(`v0-voice-check.json`)는 m4a·mp3·wav만 안내 | 실제 카카오 녹음·STT 코덱 실측과 카드 안내 정합성 확인. 이번 통합에서도 지원 제거/확대 없음 |
| 녹음 길이 상한 | 미정(`MAX_VOICE_DURATION_SECONDS=None`). WAV 측정과 가상 상한 경계 테스트만 구현. ffprobe 없음. 압축 형식의 길이를 추정하지 않음 | 운영 상한 값, 전체 형식 측정 도구/실행 환경, 측정 실패 안내 정책을 확정해야 함 |
| STT 서비스명 / W3v 배포 | `w3v-voice-consent.json` 의 `{{ external_service }}` 가 비어 있고, `block_voice_consent` 블록 ID는 이미 확정됨(`6ae335a8b074221a79bea79f`). 서비스명이 안 정해져 **W3v는 배포 보류**(이슈·README 모두 명시) | STT 서비스(엘리스 ML API 뒤의 실제 모델/제공자) 사용자 노출용 이름, 확정 후 W3v 배포 가능 여부 |

K6는 최신 FE 합의 D17을 반영했다. 나머지 미확정 항목(`.webm` 안내, `blockers` 처리, 길이 상한)은 임의로 변경하지 않았다.
