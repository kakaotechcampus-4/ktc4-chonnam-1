# [BE] 통화 녹음 보이스피싱 분석 연동 (feature/voice-fishing-ai 후속)

## 무엇

`feature/voice-fishing-ai` 에서 AI 쪽 통화 분석을 마쳤습니다. 백엔드가 녹음을 받아 이 함수들을 부르고, 결과를 카카오 콜백 카드로 보내는 연동이 남았습니다. 흐름은 `docs/designs/voice-phishing-flow.md`(FE 초안), 시간 예산은 `docs/latency-budget.md` 통화 행, 콜백 구조는 `docs/adr/0003-kakao-callback.md` 를 따릅니다.

AI 진입점 (`ai.case`)

| 함수 | 역할 |
| --- | --- |
| `transcribe_call(audio: bytes, filename: str, *, runtime=None) -> TranscriptResult` | whisper STT + Sonnet 5.5 화자 분리. 내부 한도 STT 30초 + 분리 20초 |
| `analyze_call(transcript, *, runtime=None, time_budget_s=40.0) -> VoiceCaseResult` | 주장 읽기 + 사례 검색 + 결정적 판정 |
| `warm_up_voice_case_search()` | 사례 색인 미리 적재 |
| `response_actions_for(answers, evidence)` (`ai.case.actions`) | Q1 답 -> 피해 대응 목록 |

결과 형태: `contracts/schemas/voice-case-result.schema.json` (`VoiceCaseResult` 에서 생성, 어긋나면 AI 테스트가 실패합니다).

## 흐름

1. 카카오 폴백 블록으로 `userRequest.utterance` 에 녹음 URL(`talk.kakaocdn.net/…m4a?credential=…&signature=…`)이 옵니다. `params.media` 는 없습니다.
2. BE가 URL을 검증하고 내려받습니다.
   - 호스트는 카카오 CDN만 허용 (SSRF 방어, 기존 `url_utils`/`callback_guard` 규칙과 같은 수준)
   - 크기 상한 약 25MB (whisper 한도), 형식 m4a·mp3·wav·webm. Content-Type 은 `etc/m4a` 처럼 표준이 아니므로 확장자로 판단
   - 초과·형식 불가는 분석 없이 안내 카드
3. **첫 응답은 즉시** 보냅니다 (콜백 사용, 5초 안). 이후 작업은 콜백 경로에서 수행합니다.
4. `transcribe_call(audio, filename)`. 걸린 시간(`elapsed`)을 잽니다.
5. `analyze_call(transcript, time_budget_s = 45 - elapsed)`. 45초는 콜백 유효 시간 기준 총 예산이고 STT·분리는 이 안에 들어갑니다. 남은 예산이 너무 적으면(예: 5초 미만) 호출하지 않고 대본 없음/미확인 카드로 처리합니다.
6. 결과를 **먼저 저장** (콜백은 1회성) -> 콜백 카드 전송. 콜백이 만료·실패하면 `결과 확인` 조회가 저장본을 보여줍니다 (처리 중이면 K1, 실패면 R4, 없으면 K4).
7. `conclusion == suspected` 이면 KISA/1394 인계 버튼을 붙입니다 (카드 규칙은 FE 초안 1.5절).

한 사용자가 분석 중에 또 보내면 K5 (문자와 같은 잠금 규칙).

## 기동 시

`backend/src/server/lifespan.py` 에서 `warm_up_kb()` 옆에 `warm_up_voice_case_search()` 를 부릅니다. 콜드 적재가 약 1.4초라 1초 검색 예산을 넘기기 때문입니다. 실패해도 서버는 떠야 하고(기존 `warm_up_kb` 와 같은 처리), 부르지 않으면 첫 통화 요청이 적재 비용을 떠안아 `similar_cases:timeout` 이 됩니다.

## 환경 변수

엘리스 ML API 는 모델마다 Base URL 이 다릅니다.

| 이름 | 용도 | 기본값 |
| --- | --- | --- |
| `STT_BASE_URL` | whisper 엔드포인트 | 필수 |
| `STT_MODEL` | STT 모델 | `whisper-large-v3` |
| `DIARIZE_BASE_URL` | 화자 분리용 Claude 엔드포인트 | 필수 |
| `DIARIZE_MODEL` | 화자 분리 모델 | `claude-sonnet-5-5` |
| `LLM_API_KEY` | 위 호출 공용 키 | 필수 |
| `LLM_BASE_URL` / `LLM_MODEL` / `CLAIMS_MODEL` | 주장 읽기 (기존, 읽기만) | 기존 값 |

Render 환경 변수와 `.env.example` 에 추가해야 합니다.

## 결과 -> 카드 매핑

| `VoiceCaseResult` | 카드 |
| --- | --- |
| `conclusion` (`suspected` / `inconclusive` / `not_analyzable` / `input_required`. 통화에는 `official_domain` 이 나오지 않음) | V1 결과 카드 종류 선택 |
| `caution` | 주의 문구 덧붙임 여부 |
| `stop_actions` | 지금 멈출 행동 목록 |
| `claims`, `evidence` (`quote`, `origin`) | 근거 줄. 원문 인용만 보여주고 LLM 문장으로 바꾸지 않음 |
| `number_checks` | 상대가 말한 번호의 공식 대표번호 대조 줄 (`org_not_listed` 는 "공식이 아니다"가 아님) |
| `checked` / `unchecked` (+`reason`) | 확인한 것 / 확인하지 못한 것. 둘을 섞지 않음 (`not_run` 과 `failed` 구분) |
| `failures` | 실패 이유 문구 (`timeout`, `llm_error`, `invalid_output` 등). 화자 분리 실패는 여기가 아니라 `unchecked` 와 `inconclusive`·`caution` 으로 나타남 |
| `similar_cases` | 비슷한 사례 (있을 때만) |
| `explanation` | 부가 설명. 비어 있어도 카드는 나가야 함 |
| `questions[0]` (Q1, `option_ids`: `entered_info`, `installed_app`, `sent_money`, `not_yet`) | 결과 아래 `이미 하신 게 있나요?` 리스트 카드. 라벨은 FE |

Q1 답은 쌓입니다. 답이 들어올 때마다 지금까지의 답과 저장된 `evidence` 로 `response_actions_for(answers, evidence)` 를 불러 대응 카드(지급정지, 112, 비밀번호 변경, 노출 등록, 앱 삭제 등)를 만듭니다. 순서는 함수가 정한 순서를 그대로 씁니다. 앱 설치 답에는 전화 버튼을 달지 않습니다 (FE 초안 1.5절).

## 개인정보

- 분석 경로의 `segments` 는 정규식으로 가려진 상태입니다 (`mask_pii`). 기관명·전화번호는 근거로 써야 해서 남깁니다.
- 대본을 **저장하거나 외부로 보내기 전**에는 주입형 `EliceMasker` (`ai/src/ai/case/privacy.py`)로 한 번 더 가립니다. 엔드포인트 주소는 아직 미정이라 `base_url`/`api_key`/`timeout` 을 설정으로 받게 하고, 미정이면 대본은 저장하지 않고 결과(`VoiceCaseResult`)만 저장합니다.
- 녹음 원본은 분석 직후 폐기합니다 (저장 금지). 녹음 전용 전송 동의(W3v)는 FE 초안 VF4 를 따릅니다.

## 알려진 한계 / 후속

- 일부 오디오 바이트가 Cloudflare 403 으로 막힙니다. voice-stt 참고 구현은 gain 0.99 -> 0.97 로 바꿔 재시도합니다. 디코딩이 필요해서 BE 쪽 일입니다 (AI는 바이트만 받음).
- `audio_seconds` 는 wav 만 채워집니다. 그 외 형식은 `None` 입니다.
- 지식베이스 대본의 라이선스 문제: 금감원 자료는 "무단 복제·배포 금지"입니다. 공개 저장소에 올리기 전에 해결해야 합니다.
- 가족·지인 사칭 유형은 사례 검색이 약합니다 (결과가 `inconclusive` 로 나올 수 있음).
- 5초 안에 카카오 첫 응답, STT 실측은 3~4분 녹음에 12~16초입니다. 더 긴 녹음의 한도는 정해지지 않았습니다.

## 완료 조건

- [ ] 녹음 URL 검증(호스트·크기·형식)과 내려받기, 초과·불가 시 안내 카드
- [ ] 첫 응답 즉시, 이후 `transcribe_call` -> `analyze_call(time_budget_s = 45 - elapsed)` 를 콜백 경로에서 실행
- [ ] 결과를 콜백 전에 저장하고, `결과 확인` 이 저장본(처리 중 K1 / 실패 R4 / 없음 K4)을 보여줌
- [ ] V1 결과 카드와 Q1 리스트 카드 렌더링, Q1 답은 `response_actions_for` 결과로 대응 카드 생성
- [ ] `suspected` 일 때 KISA/1394 인계 버튼
- [ ] lifespan 에서 `warm_up_voice_case_search()` 호출 (실패해도 기동)
- [ ] 환경 변수 6개를 `.env.example`·Render 에 반영
- [ ] 대본 저장·외부 전송 전 마스킹 (엔드포인트 확정 전에는 대본 비저장)
- [ ] STT 실패(`not_analyzable`), 화자 분리 실패(`TranscriptResult.speaker_split_failed`), 시간 초과 경로 테스트
- [ ] 한 사용자 동시 요청 K5 잠금
- [ ] 의존 방향 유지 (`backend` -> `ai`), `ai/` 는 카카오 payload 를 모름
- [ ] 관련 테스트와 전체 테스트 통과
