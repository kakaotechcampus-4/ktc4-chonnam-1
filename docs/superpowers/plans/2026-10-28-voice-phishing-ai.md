# 보이스피싱 분석 AI 파트 구현 계획

- Spec: `docs/superpowers/specs/2026-10-28-voice-phishing-ai-design.md` (이하 "스펙")
- 설계 원문: `docs/designs/agent-loop-plan-v3.md` (이하 "v3 §N")
- 브랜치: `feature/voice-fishing-ai`

## Global Constraints

1. **수정 범위는 `ai/` 와 `docs/` 뿐이다.** `main.py`, `backend/`, `services/`, `scanner/`, `frontend/`, `infra/`, 루트 `tests/` 는 고치지 않는다. 단, Task 1은 `D:\voice\` 에 쓴다.
2. `ai/` 안에서 `server`·`backend`·`services`·`scanner`·`main` 을 import 하지 않는다(CLAUDE.md 4).
3. 기존 공개 함수(`ai.pipeline.*`, `ai.llm.*`, `ai.kb.*`, `ai.verdict.*`)의 동작을 바꾸지 않는다. 특히 스미싱 `search_cases` 의 기본 동작·파라미터·결과는 그대로다.
4. 테스트는 실제 LLM·네트워크를 부르지 않는다. 가짜 클라이언트(`ai/tests/conftest.py` 의 `SimpleNamespace` + `AsyncMock` 패턴)나 `httpx.MockTransport` 를 쓴다.
5. 테스트 명령(저장소 루트): `.venv/Scripts/python.exe -X utf8 -m pytest ai/tests -q`. 시작 기준 **971 passed, 1 skipped**. 매 Task 끝에 전체가 통과해야 한다.
6. 새 타입은 `ai.types.StrictModel`(extra="forbid") 기반. 실패 코드는 기존 `ai.types.FailureCode` 를 쓴다.
7. LLM 호출은 재시도하지 않는다(`max_retries=0`, 기존 `ai.llm._client.create_client(timeout)`). 단계 예산은 부르는 쪽이 정한다(ADR 0004).
8. 주석·docstring은 주변 코드처럼 한국어, 같은 밀도로 쓴다. "왜"만 적는다.
9. 커밋: `feat(ai): …` / `test(ai): …` / `docs: …`. 메시지 끝에 빈 줄 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
10. 이 계획은 **통화(audio) 경로만** 만든다. 문자·캡처 경로, `apply_answer`, Q2, 페이지 조사는 만들지 않는다.

---

### Task 1: 보이스피싱·정상 통화 데이터 수집 (`D:\voice\`)

**목표:** 사례 검색(Task 6)과 평가에 쓸 통화 대본 데이터셋을 만든다. 저장소에는 아무것도 커밋하지 않는다.

**규칙 (스펙 D5·D6):**
- 저작권으로 **명시적으로 이용이 막힌** 자료만 뺀다(예: "무단 전재·재배포 금지"를 명시한 언론 기사 원문, 이용약관상 재사용 금지 데이터셋). 그 밖은 쓴다. 출처마다 라이선스·이용조건 문구를 그대로 기록한다.
- 검수 없이 진행한다. 유형 라벨과 합성 대본도 직접 만든다.
- 원본 영상·음성: `D:\voice\raw\<source>\`. 중간 산출물(받아쓰기 원문 등): `D:\voice\transcripts\<source>\`.

**출처:**
1. 금감원 '바로 이 목소리' 게시판(https://www.fss.or.kr/fss/bbs/B0000203/list.do?menuNo=200686) 101건. **이미 받아쓴 원문**이 `C:\Users\ksy\OneDrive\바탕 화면\voice-stt\fss\전체받아쓰기\*.plain.txt`(101개)에 있고, 출처 목록은 `...\voice-stt\fss\sources.csv` 다. 다시 받아쓰지 말고 복사해 쓴다. 영상 원본은 필요 없다.
2. 추가 보이스피싱 출처를 찾는다. 후보: 금감원의 다른 보이스피싱 음성 공개 게시판/이전 '그놈 목소리' 자료, 경찰청·검찰·금감원 공식 유튜브의 실제 통화 공개 영상, 공개 연구 데이터셋(예: KorCCVi — Korean Call Content Vishing dataset, GitHub·Kaggle), 그 밖의 공개 대본. 목표: 보이스피싱 **200건 이상**(FSS 포함).
3. 정상 통화: 공개 자료를 먼저 찾는다(예: KorCCVi 의 정상 통화, 공개 한국어 상담·전화 대화 코퍼스). 부족하면 **합성 대본**으로 채운다. 보이스피싱과 헷갈리기 쉬운 정상 통화를 우선한다: 은행·카드사 고객센터, 택배 기사, 병원·관공서 안내, 실제 경찰·검찰의 정상 연락, 대출 상담사, 가족 통화. 목표: **100건 이상**.
4. 음성·영상만 있는 새 자료는 받아쓴다. whisper 호출은 `C:\Users\ksy\OneDrive\바탕 화면\voice-stt\stt.py` 의 `make_clients()`·`to_audio()`·`transcribe()`·`transcribe_samples()` 를 import 해서 쓴다(그 폴더 `.venv\Scripts\python.exe` 로 실행, 설정은 그 폴더 `.env` 를 코드가 읽는다. `.env` 파일을 직접 열거나 출력하지 않는다). 화자 분리는 하지 않는다.

**산출물:**
- `D:\voice\dataset\voice_calls.jsonl` — 한 줄에 한 통화:
  `{"id": "<source>-<원본id>", "label": "phishing" | "normal", "type": <유형>, "type_source": "title" | "model" | "synthetic", "source": "<출처 이름>", "source_url": "<게시물/파일 URL 또는 null>", "license": "<이용조건 문구 또는 확인 결과>", "published": "YYYY-MM-DD" | null, "group": "<같은 원본·같은 범죄자·같은 합성 템플릿 묶음 키>", "synthetic": true|false, "text": "<받아쓴 대본 전체, 한 줄>"}`
- 유형(`type`) 값은 다음 중 하나다: `institution_impersonation`(검찰·경찰·금감원·법원 등 수사·감독기관 사칭), `loan_fraud`(대출 빙자·대환·선입금), `family_impersonation`(가족·지인 사칭), `kidnapping`(납치·협박 빙자), `account_recruitment`(대포통장·알바 모집), `other_impersonation`(그 밖의 기관·회사 사칭: 출입국·택배·통신사·은행 등), `other`. 정상 통화는 `type` 에 상황(`bank_service`, `delivery`, `public_office`, `loan_consult`, `family`, `police_real`, `other_normal`)을 쓴다.
- 제목에 유형이 드러나면 `type_source: title`, 대본을 읽고 붙였으면 `model`, 합성이면 `synthetic`.
- `text` 는 저장 전에 주민번호(`\d{6}-?[1-4]\d{6}`)·카드번호(13~16자리, 구분자 허용)·계좌형 10~14자리 숫자열을 `<주민번호>`·`<카드번호>`·`<계좌번호>` 로 바꾼다. 전화번호는 남긴다.
- whisper 반복 환각(같은 짧은 문장이 5번 넘게 연달아 나옴, 예: "네. 네. 네. …")은 한 번만 남긴다.
- `D:\voice\SOURCES.md` — 출처별: 이름, URL, 라이선스/이용조건 문구, 쓴 건수, 뺀 자료와 이유, 받아쓴 방법.

**완료 기준:** `voice_calls.jsonl` 이 있고, 보이스피싱·정상 건수와 유형별 분포를 보고서에 적는다. 목표 건수에 못 미치면 찾아본 출처와 막힌 이유를 적는다.

---

### Task 2: 통화 타입과 개인정보 가리기 (`ai/src/ai/case/`)

**목표:** 1단계 "STT·화자 분리 뒤 반환 형태"와 이후 Task가 함께 쓸 타입을 정한다. 실제 STT는 만들지 않는다.

**파일:**
- 신규 `ai/src/ai/case/__init__.py`, `ai/src/ai/case/types.py`, `ai/src/ai/case/privacy.py`
- 신규 테스트 `ai/tests/test_case_types.py`, `ai/tests/test_case_privacy.py`

**`types.py`** — v3 §3.1에서 **통화에 필요한 것만**. 이름·필드·enum 값은 v3를 그대로 쓴다. 추가·변경은 아래뿐이다.
- `Segment`: `side: Side`(`other | self | unknown`), `speaker: str | None`, `text: str`, `start: float | None = None`, `end: float | None = None`. 화자 이름은 `"화자1"`, `"화자2"`, `"기타"` 같은 문자열이다(스펙 D1).
- **신규 `TranscriptResult`** (STT·화자 분리의 반환 형태, 6.5단계가 이것을 만든다): `segments: list[Segment]`, `speaker_split_failed: bool`, `failure: FailureCode | None = None`, `stt_model: str | None = None`, `diarize_model: str | None = None`, `audio_seconds: float | None = None`. 세그먼트 `side` 는 `unknown` 으로 두고 읽기 뒤에 채운다(v3 §4).
- `ReadingSource`(v3 §3.1): `kind: InputKind`, `segments`, `other_speaker: str | None`, `speaker_split_failed: bool`.
- `ClaimReading`(v3 §5.1) 중 통화에 쓰는 칸: `identity`(≤3), `pretexts`(≤3), `demands`(≤6), `blockers`(≤3), `other_speaker`, `speaker_confidence`(`high|medium|low`), `spoken_hosts`(≤3). 문자 전용 칸(`categories`·`claimed_sender`·`claimed_purpose`·`persuasion_signals`)은 넣지 않는다. 개수 제한은 pydantic 검증으로 건다.
- `ClaimReadingResult`: `status: ok | fallback`, `reading: ClaimReading | None`, `failure: FailureCode | None`.
- enum: `InputKind`, `Scenario`, `Conclusion`, `Origin`, `IdentityKind`, `DemandKind`(v3 §5.2 전부), `EvidenceCode`(v3 §7.1 중 통화에 쓰는 것: `unofficial_number`, `institution_claim`, `acquaintance_claim`, `contact_blocker`, 요구 코드 9개), `StopAction`(v3 §7.5), `ResponseAction`(v3 §7.7에 나오는 값 전부), `QuestionId`(`already_acted` 만), `CheckItem`·`UncheckedItem`·`UncheckedReason`(v3 §7.9).
- `Evidence`, `RejectedClaim`(v3 §3.1 그대로), `AttachedQuestion`, `ClaimView`.
- **신규 `VoiceCaseResult`**: v3 `CaseFinal` 에서 통화에 쓰는 칸만: `scenario`, `conclusion`, `caution`, `floors: list[str]`, `evidence`, `claims: ClaimView`, `stop_actions`, `questions`, `checked`, `unchecked`, `similar_cases: CaseSearchResult | None`, `number_checks: list[NumberCheck]`, `rejected: list[RejectedClaim]`, `reading_status`, `explanation: str`, `failures: list[FailureCode]`. (`NumberCheck` 는 Task 5가 정의하므로 여기서는 Task 5 전까지 정의하지 않는다 — 이 칸은 Task 5에서 더한다.)
- 모든 모델은 `model_dump(mode="json")` → `model_validate` 왕복이 같아야 한다(테스트).

**`privacy.py`** (스펙 D2):
- `mask_pii(text: str) -> str`: v3 §4 규칙. 주민번호 `\d{6}-?[1-4]\d{6}` → `<주민번호>`, 카드번호(13~16자리, 공백·하이픈 구분 허용) → `<카드번호>`, 계좌번호처럼 보이는 10~14자리 숫자열(하이픈 허용) → `<계좌번호>`. **전화번호(`0\d{1,2}-?\d{3,4}-?\d{4}`, `1\d{3}`, `1\d{3}-\d{4}`)와 URL은 남긴다.** 주민 → 카드 → 계좌 순서로 적용한다.
- `mask_transcript(t: TranscriptResult) -> TranscriptResult`: 세그먼트 텍스트마다 `mask_pii`.
- `Masker` Protocol: `async def mask(self, texts: list[str]) -> list[str]`.
- `EliceMasker(base_url: str, api_key: str, timeout: float)`: `POST {base_url}/mask`, 본문 `{"text": [...]}`, 헤더 `Authorization: Bearer <key>`, 응답 `[{"original_text", "masked_text"}, …]` 에서 `masked_text` 를 순서대로 돌려준다. 저장·외부 전송용이며 분석 경로에서는 쓰지 않는다(docstring에 이유: 기관명·전화번호까지 가린다). `httpx.AsyncClient` 를 쓰고, 테스트는 `httpx.MockTransport`.

**테스트:** 주민·카드·계좌가 가려지고 전화번호(`02-1234-5678`, `010-1234-5678`, `1332`, `1588-1234`)와 URL은 남는다. 왕복 직렬화. `EliceMasker` 요청 모양과 응답 매핑. 개수 제한 초과 시 검증 오류.

---

### Task 3: 주장·요구 읽기(통화 모드)와 어휘표 폴백

**목표:** 대본에서 상대 화자, 내세운 신분·명분, 요구(종류+인용), 확인을 막는 사정, 말로 부른 주소를 읽는다(v3 §5).

**파일:**
- 신규 `ai/src/ai/llm/claims.py`, `ai/src/ai/prompts/v1/claims_call.md`, `ai/src/ai/case/lexicon.py`
- 신규 테스트 `ai/tests/test_claims.py`, `ai/tests/test_case_lexicon.py`

**`read_claims(source: ReadingSource, *, client=None, model=None, timeout_s: float = 12.0) -> ClaimReadingResult`**
- 통화 모드만 지원한다(`source.kind == audio`). 다른 kind면 `ValueError`.
- 모델: 인자 → 환경변수 `CLAIMS_MODEL` → `LLM_MODEL`(스펙 D3). 클라이언트가 없으면 `ai.llm._client.create_client(timeout_s)`.
- `client.chat.completions.parse(model=…, response_format=ClaimReading, temperature=0, messages=[system, user])` 를 **한 번** 부른다. `asyncio.wait_for(…, timeout_s)`.
- 프롬프트 `claims_call.md` 는 v3 §5.3의 1~8을 모두 담는다. 세그먼트는 `<data speaker="화자1">…</data>` 로 한 줄씩 감싼다(원문 순서).
- 시간 초과 → `FailureCode.TIMEOUT`, API 오류 → `LLM_ERROR`, 거절 → `REFUSED`, 파싱 실패·빈 결과 → `INVALID_OUTPUT`. 이 경우 `lexicon.read_with_lexicon(source)` 결과를 `status=fallback`, `failure=<원인>` 으로 돌려준다. `asyncio.CancelledError` 는 그대로 올린다.
- 프롬프트 로딩은 기존 `ai/src/ai/llm/analyze.py` 의 방식을 따른다.

**`lexicon.py`** — v3 §5.6 그대로: 문장 단위 요구 종류 우선순위 표(1~9), identity(V-5 기관 목록 → institution, 관계 어휘 → acquaintance), blockers 표현, 통화 상대 화자 고르기(걸린 수가 가장 많은 화자, 없거나 동률이면 `speaker_split_failed`에 해당하도록 `other_speaker=None`, `speaker_confidence=low`). 인용은 원문 문장을 그대로 쓴다. 어휘 표는 모듈 상수로 둔다.

**테스트:** 가짜 클라이언트로 정상 파싱, 시간 초과 → 폴백(`failure=timeout`), 예외 → 폴백(`llm_error`), 프롬프트에 세그먼트가 `<data speaker=…>` 로 들어가는지, 모델 선택 순서. 어휘표: v3 §5.6 표의 각 줄 양성 예 1개 이상, "검사 결과"·"수사 중인"이 기관으로 안 걸림, v3 §9 V1 대본에서 B가 상대 화자로 골라짐.

---

### Task 4: 읽기 검증 (`ai/src/ai/case/verify.py`)

**목표:** v3 §5.5 V-1~V-8을 통화에 대해 구현한다. LLM이 낸 주장 중 실제 대본에 근거가 있는 것만 남긴다.

**파일:** 신규 `ai/src/ai/case/verify.py`, 테스트 `ai/tests/test_case_verify.py`

**`verify_call(source: ReadingSource, reading: ClaimReading, *, derived_by: Literal["llm", "lexicon"]) -> VerifyResult`**
- `VerifyResult`(이 파일 또는 types.py에 둔다): `accepted: list[Evidence]`, `rejected: list[RejectedClaim]`, `other_speaker: str | None`, `speaker_split_failed: bool`, `spoken_hosts: list[str]`, `claims: ClaimView`.
- V-1: 인용이 (마스킹된) 세그먼트 텍스트 중 하나에 그대로 있고 4자 이상. 아니면 `not_in_source`/`too_short`.
- V-2: `other_speaker` 세그먼트에 있는 인용만. 화자 분리 실패면 전체 허용. 아니면 `self_side`.
- V-3: `demands` 에 v3 표의 알림 문장 끝 모양 정규식 4개. 물음표로 끝나면 검사하지 않는다. → `notice_sentence`.
- V-4: `demands` 에 기존 `ai.message_requests.action_is_denied(text, action_end)` 가 참이면 `negated`. `text` 는 인용이 든 세그먼트, `action_end` 는 그 안에서 인용이 끝나는 위치다.
- V-5: identity(kind=institution)의 `name` 또는 `quote` 가 `검찰|지검|검사(?:님|입니다|실)|수사관|경찰|금융감독원|금감원|금융위원회|법원` 에 걸리면 `institution_claim` 근거. 안 걸리면 `ClaimView.identity` 에만 남긴다(`listed_institution=False`).
- V-6: acquaintance 는 V-1·V-2 통과 시 `acquaintance_claim`.
- `blockers` 는 V-1·V-2 통과 시 `contact_blocker`.
- 요구 종류 → 근거 코드 매핑은 v3 §5.2 표. `visit_link`·`other` 는 근거 코드 없이 `ClaimView.demands` 에만 남긴다.
- V-7: `spoken_hosts` 를 호스트 형식(`^[a-z0-9-]+(\.[a-z0-9-]+)+$`, 소문자화, 경로·쿼리 제거)으로 검증. 실패면 `not_url`.
- V-8: `speaker_confidence == low` 이거나 `other_speaker` 가 없거나 세그먼트에 없는 화자면 `speaker_split_failed=True`. `source.speaker_split_failed` 가 이미 참이면 그대로 참.
- 모든 `Evidence` 는 `origin=transcript`, `derived_by` 는 인자, `counts_for_floor` 는 근거 코드가 L-3 조합에 쓰이면 참(v3 §7.1 표).
- 버린 항목은 `rejected` 에 사유와 함께.

**테스트:** v3 §9 N3(V-5 목록 밖 → institution_claim 없음), N4(둘째 문장 money_transfer → V-3로 버림), V-2 self_side, V-4 부정문("앱 설치를 요구하지 않습니다"), V-7 "spo-case.com/case?name=x" → `spo-case.com`, V-8 low → split failed, 화자 분리 실패 시 V-2 허용.

---

### Task 5: 기관 대표번호 KB와 번호 대조

**목표:** 상대가 말한 번호가 내세운 기관의 공식 대표번호인지 로컬에서 대조한다(v3 §6.3·§6.5, 스펙 D4).

**파일:**
- 신규 `ai/src/ai/kb/official_numbers.yaml`, `ai/src/ai/kb/numbers.py`
- 수정 `ai/src/ai/case/types.py`(`NumberCheck`, `VoiceCaseResult.number_checks` 추가)
- 신규 테스트 `ai/tests/test_numbers.py`

**번호 수집:** 각 기관 **공식 누리집**에서 대표번호를 확인해 넣는다. 항목마다 `org`(정식 이름), `aliases`(문장에서 쓰는 이름: 예 금감원·금융감독원), `numbers`(정규형 문자열 목록), `source_url`, `checked_at: 2026-10-28`, `status: curated`(출처 URL이 있을 때, 스펙 D4. 확인 못 하면 `draft`). 최소 대상: 금융감독원, 금융위원회, 대검찰청(검찰·지검 별칭), 경찰청(112·182), 경찰청 전기통신금융사기 통합대응단(1394, 이전 번호 병행 여부도 기록), 한국인터넷진흥원(118), 법원(대법원 대표번호), 국세청, 국민건강보험공단, 국민연금공단, 우정사업본부(우체국), 출입국·외국인정책본부(1345), 그리고 사칭이 잦은 시중은행·카드사 대표번호(KB국민·신한·우리·하나·NH농협·IBK기업·카카오뱅크·토스뱅크, 주요 카드사). 확인 못 한 기관은 넣지 않는다.

**`numbers.py`:**
- `normalize_number(raw: str) -> str | None`: 공백·하이픈·괄호·`+82` 처리, 숫자만 남긴 정규형. 전화번호 모양이 아니면 None.
- `find_numbers(text: str) -> list[str]`: 문장 속 전화번호 후보(정규형). 한국어 숫자 읽기("일삼삼이")는 다루지 않는다.
- `resolve_org(name_or_quote: str) -> str | None`: aliases 로 기관을 찾는다.
- `check_phone_number(number: str, org: str) -> NumberCheck`: `NumberCheck{number, org, result: official | not_official | org_not_listed, source_url}`. `status: curated` 항목만 쓴다.
- YAML 로드는 `functools.lru_cache`. 기존 `kb/search.py` 의 로딩 방식을 따른다.

**테스트:** 정규화(`02-1234-5678`, `(02)1234-5678`, `+82-2-1234-5678`, `1332`, `1588-0000`), 금감원 1332 → official, 금감원 + 02-1234-5678 → not_official, 모르는 기관 → org_not_listed, draft 항목은 쓰지 않음, YAML 스키마 검사(모든 curated 항목에 source_url).

---

### Task 6: 통화 사례 검색(voice KB)과 hit@3 조정

**목표:** 스미싱 `search_cases` 와 같은 구조로 voice KB를 검색한다. 데이터와 파라미터만 다르다(스펙 D7·D8). dev hit@3을 반복 조정해 **test hit@3 ≥ 60%, 가능하면 80%**.

**입력:** Task 1의 `D:\voice\dataset\voice_calls.jsonl`.

**파일:**
- 신규 KB `ai/src/ai/kb/voice_cases/VC-NNNN.md`(KB 분할의 보이스피싱 통화만) + `ai/src/ai/kb/voice_cases/README.md`
- 신규 평가셋 `ai/eval/datasets/voice_rag.jsonl`(dev·test 분할, 보이스피싱+정상)
- 수정 `ai/src/ai/kb/search.py`: voice 검색 진입점 추가. **스미싱 기본 동작은 그대로.** 권장: `search_voice_cases(text, *, min_similarity=None, top_k=…)` 를 새로 두고, 내부에서 KB 폴더·파라미터를 voice 값으로 바꾼 같은 색인 코드를 재사용한다. 파라미터는 모듈 상수 `VOICE_NGRAM_SIZES`, `VOICE_SUBLINEAR_TF`, `VOICE_MIN_SIMILARITY` 등으로 둔다.
- 신규 `ai/eval/voice_rag_eval.py`(+ 필요하면 `ai/eval/rag_metrics.py` 재사용), 보고서 `ai/eval/reports/2026-10-28-voice-rag-*.md`
- 신규/수정 테스트: `ai/tests/test_voice_search.py`, `ai/tests/test_kb_data.py`(voice KB 레코드 형식 검사 추가)

**분할:** `group` 단위로 KB 60% · dev 20% · test 20%(같은 group이 두 분할에 걸치지 않게, 시드 고정). 정상 통화는 KB에 넣지 않고 dev·test에만 둔다(정상 부착률 측정용).

**KB 레코드 형식:** 기존 `case_examples/CE-*.md` frontmatter와 같은 모양에 `type`(Task 1 유형), `source`, `source_url`, `license`, `synthetic` 을 더한다. `status: curated`, `reviewer: auto-import-2026-10-28`(스펙 D5). `variants` 는 대본(길면 상대 화자 위주로 자르지 말고 대본 전체 — 길이 상한이 필요하면 근거를 보고서에 적는다), `normalized` 는 기존 `normalize()`.

**지표(dev로 조정, test는 마지막 한 번):**
- hit@3: 보이스피싱 질의에서 상위 3개(최소 유사도 이상) 중 같은 `type` 사례가 있는 비율.
- 같이 보고: hit@1, MRR, 정상 통화 부착률(최상위가 최소 유사도 이상인 비율), **다수 유형 기준선**(가장 흔한 유형을 늘 맞혔다고 칠 때의 비율) — hit@3이 기준선보다 의미 있게 높아야 한다.
- 조정 대상: n-gram 크기 조합, sublinear TF, 최소 유사도, variant 분할(대본을 여러 조각으로 나눠 조각마다 문서로 둘지), 질의 전처리(상대 화자 발화만/전체). 각 시도를 보고서 표에 한 줄씩 남긴다(설정, dev hit@3, hit@1, 정상 부착률).
- 같은 group이 KB와 질의에 함께 있으면 안 된다(누수 검사 테스트).

**완료 기준:** 최종 설정으로 test hit@3 ≥ 60%. 못 미치면 시도 기록과 원인 분석을 보고서에 남기고 가장 좋은 설정으로 둔다. 스미싱 평가(`ai/eval/rag_eval.py`)와 기존 테스트 결과가 바뀌지 않는다.

---

### Task 7: 게이트·행동·설명 (통화 시나리오)

**목표:** 검증된 근거로 결론을 정하는 순수 함수(v3 §7, 통화 줄만).

**파일:** 신규 `ai/src/ai/case/gate.py`, `ai/src/ai/case/actions.py`, `ai/src/ai/case/explain.py`, 테스트 `ai/tests/test_case_gate.py`

**`decide_call(*, transcript_empty: bool, verify: VerifyResult | None, number_checks: list[NumberCheck], similar: CaseSearchResult | None, failures: list[FailureCode], reading_status) -> VoiceCaseResult`** (이름은 바꿔도 된다. LLM·네트워크·시계 없음)
- 시나리오는 늘 `voice_phishing`.
- U-6: 대본이 비었거나 STT 실패 → `not_analyzable`(멈출 행동·질문 없음).
- L-3: 상대 화자의 `institution_claim` + (`money_request`·`app_install_request`·`remote_app_mention`·`info_request`·`auth_code_request`·`cash_delivery_request` 중 하나), 또는 `acquaintance_claim` + `money_request` → `suspected`, floors `["L-3"]`.
- U-4: `speaker_split_failed` 이면 L-3을 쓰지 않는다 → `inconclusive`, `caution=True`.
- U-5: 받아들인 요구 코드·`institution_claim`·`acquaintance_claim` 이 모두 없으면 `input_required`. (통화에는 링크 후보 조건이 없다. `spoken_hosts` 는 이번 범위에서 링크 조사하지 않으므로 U-5 판단에 쓰지 않는다.)
- 그 밖은 `inconclusive`. `caution` 은 v3 §7.4(통화에 해당하는 줄: 위험 요구, `speaker_split_failed`, `unofficial_number`, `institution_claim`, `acquaintance_claim` + `messenger_move`/`contact_blocker`).
- `unofficial_number`(`NumberCheck.result == not_official`)는 보조 근거다. 하한에 쓰지 않는다.
- 멈출 행동: v3 §7.5 표 순서로 모아 최대 3개, `hang_up` 은 voice 시나리오 줄. 결론이 `input_required`·`not_analyzable` 이면 없음. (`dont_click_link` 은 링크 후보가 없으므로 나오지 않는다.)
- 질문(Q1): v3 §7.6 — `suspected` 이거나 `inconclusive`+`caution` 일 때. `option_ids` = `entered_info`, `installed_app`, `sent_money`, `not_yet`. **`clicked_link` 는 넣지 않는다.** `inconclusive`+`caution=False` 에는 붙이지 않는다(링크가 없으므로).
- `actions.py`: `response_actions_for(answers: list[str], evidence) -> list[ResponseAction]` — v3 §7.7 통화 관련 줄(sent_money·gift_card, entered_info + `info_request` 인용에 "주민", entered_info 그 밖, installed_app → `remove_app_and_check_device` + `request_payment_stop`). 표 위에서부터 합치고 중복 제거. `default_response_actions(option_id)` 도 v3 그대로.
- checked/unchecked: v3 §7.9 — `transcript` 는 checked, `phone_number` 는 대조했으면 checked, `similar_cases` 는 결과가 있으면 checked·`fallback` 이면 `kb_empty`. `actual_sender:cannot_verify` 는 늘 unchecked. 받아쓰기 정확도는 `transcript_accuracy:read_from_voice`(FE V1 "받아 적은 글이 정확한지" — `UncheckedItem` 에 값이 없으면 더한다).
- `explain.py`: 근거 코드별 템플릿 문장만(v3 §7.10). 금지어(`안전합니다`·`안전한`·`정상입니다`·`정상 문자`·`문제없`·`걱정 마`)가 템플릿에 없음을 테스트로 고정.

**테스트:** 결론 표(L-3 두 갈래, U-4, U-5, U-6, caution 갈래), 멈출 행동 순서·상한, Q1 option_ids, 대응 합치기 순서, 금지어.

---

### Task 8: 통화 분석 진입 함수와 사례 테스트

**목표:** Task 2~7을 묶어 BE가 부를 하나의 함수로 만든다. 질문으로 멈추지 않고 늘 결과를 낸다.

**파일:** 신규 `ai/src/ai/case/voice.py`, `ai/src/ai/case/__init__.py` export, 테스트 `ai/tests/test_case_voice.py`, 사례 픽스처 `ai/tests/fixtures/cases/voice/*.yaml`, 문서 `docs/latency-budget.md` 에 통화 줄 추가

**`async def analyze_call(transcript: TranscriptResult, *, runtime: VoiceRuntime | None = None, time_budget_s: float = 40.0) -> VoiceCaseResult`**
- `VoiceRuntime`(dataclass): `read_claims` (기본 Task 3), `search_cases` (기본 Task 6 `search_voice_cases`, 동기 함수는 `asyncio.to_thread`), `check_phone_number`(기본 Task 5). 테스트에서 가짜로 바꾼다.
- 순서: `transcript.failure` 또는 빈 대본 → 읽지 않고 게이트(U-6, LLM 0). 그 밖: `mask_transcript` → `ReadingSource` → `read_claims` ∥ 사례 검색(질의는 마스킹 대본 전체. 상대 화자 발화만 쓰는 편이 Task 6에서 더 나았으면 그 방식) → `verify_call` → 세그먼트의 `side` 를 `other_speaker` 기준으로 채움 → 받아들인 `institution_claim` 기관이 KB에 있고 상대 발화에 번호가 있으면 번호마다(최대 2개) `check_phone_number` → 게이트.
- 읽기 예산 12초(v3 §6.4), 사례 검색 1초. 전체 `time_budget_s` 를 넘기면 남은 단계를 건너뛰고 `unchecked` 에 `timeout`.
- 예외를 던지지 않는다(`CancelledError` 만 올린다). 실패는 `failures`·`unchecked` 에.
- `docs/latency-budget.md` 에 통화 줄: STT·화자 분리(6.5에서 확정, 2026-10-28 실측 3~4분 녹음 10~15초), 주장 읽기 12초, 사례 검색 1초.

**사례 테스트(가짜 읽기, v3 §9 기대대로):** V1(`suspected`, L-3, 멈출 행동 [dont_send_money, dont_call_number_in_message, hang_up], Q1 [entered_info, installed_app, sent_money, not_yet], 번호 대조 금감원 + 02-1234-5678 → unofficial_number), N3(`inconclusive`, caution, [dont_send_money, hang_up]), N4(`inconclusive`, caution, [hang_up], Q1), N10(빈 대본 → `not_analyzable`, 읽기 호출 0), N11(화자 정보 없음 → U-4 `inconclusive`, caution), N14(읽기 시간 초과 → 어휘표 폴백 → `suspected`, L-3, `failures` [timeout], `derived_by=lexicon`). 사례 픽스처 YAML은 v3 §9.0 형식에서 audio에 필요한 키만 쓴다(`transcript: [[화자, 문장], …]`, `runtime.reading`, `expect`).
