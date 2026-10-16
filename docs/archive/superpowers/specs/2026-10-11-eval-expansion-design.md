# RAG 평가셋 확장과 안내 평가 설계 (로드맵 4단계)

- 작성: 2026-10-11
- 상태: 승인 (사용자, 2026-10-11)
- 범위: PR #39 멘토 RAG 피드백 로드맵 4단계
- 관련: `docs/superpowers/specs/2026-10-11-norm-guidance-design.md` 5절(결정 보류), `ai/eval/datasets/README.md`, `ai/eval/rag_eval.py`

## 1. 목적

멘토 피드백(PR #39 리뷰):

> 평가에는 사칭 문자뿐 아니라 정상 전자고지, 명분은 같지만 요구행동이 다른 문자, 기관·관할을 확인하기 어려운
> 문자도 넣어 주세요. 같은 유형의 사례를 찾았다는 것만으로 올바른 업무 절차를 안내했다고 볼 수는 없습니다.
> … 근거의 정확성·정상 문자 오탐·확인 경로의 적절성도 함께 살펴보세요. 제보받은 문자는 개인정보와 출처를
> 관리하고, 제보됐다는 이유만으로 악성 라벨을 붙이지 마세요.

지금 평가는 "같은 유형의 사례를 찾았나"(hit@k)만 잰다. 3단계에서 만든 `find_guidance()` 로 **맞는 확인
방법을 안내했나**를 직접 잰다.

성공 기준: 리포트 하나에서 새 문자 세 종류별로 (1) 사례 검색 정확도 (2) 확인 방법 안내의 맞음·빠짐·엉뚱함
(3) 정상 문자를 잘못 다룬 비율을 볼 수 있다. 그리고 해외결제·과태료 놓침 비율 등 보류한 결정(주제 추가,
NR-0019 조건, 택배사 인식 보완)의 근거 수치가 나온다.

## 2. 원칙

- 평가는 **LLM 없이 고정 규칙만** 쓴다. 같은 입력이면 같은 점수, 비용 0. 운영과의 차이(AI 추출 요구 행동·
  택배사 미포함)는 리포트에 한계로 적는다.
- 판정 코드(`ai/verdict.py`), 스키마(`ai/src/ai/types.py`), 주제(`Topic`) 값은 바꾸지 않는다.
- 데이터는 **공식 출처에 실린 예시를 우선** 쓰고, 모자란 칸만 합성한다. **합성 문자는 파일에 넣기 전에 사용자
  검수를 받는다.**
- 기존 데이터 규칙을 지킨다: 주소를 지운 `message`, 개인정보 마스킹, KB 와 겹치지 않음, dev·test 분할.

## 3. 평가 데이터

### 3.1 새 문자 36건 (`ai/eval/datasets/rag_testset.jsonl` 에 추가)

| 종류 (`kind`) | 그룹 | 택배 | 과태료 | 해외결제 |
|---|---|---|---|---|
| `official_notice` 정상 전자고지 | benign | 4 | 4 | 4 |
| `same_pretext` 같은 명분·다른 요구 | smishing 2 + benign 2 | 4 | 4 | 4 |
| `unclear_org` 관할 불명 | smishing | 4 | 4 | 4 |

- `same_pretext` 예: "주소 불일치로 배송 보류" 명분에 요구가 ① 링크로 주소 수정(사칭) ② 문자 속 번호로
  전화(사칭) ③ 공식 앱에서 확인(정상) ④ 고객센터 안내만(정상).
- `unclear_org` 예: "[과태료] 미납 고지 확인 바랍니다"처럼 기관·지역이 없는 문자.
- 칸마다 dev 2 · test 2 (`same_pretext` 는 사칭·정상 각 1건씩 dev·test). 칸별 수치는 참고용이고 종류별 합계(12건)로
  해석한다. 같은 `source` 에서 나온 행은 같은 split 에 둔다(`test_rag_testset_split_unit_stays_in_one_split`).
- id: `K-<kind>-<business>-NN` (예: `K-official_notice-penalty-01`).

### 3.2 출처와 검수

- `origin: official_example` — 기관 공지·정부 안내에 실린 예시. `source` URL 필수. 원문을 내려받아 기계로
  대조하고, 대조 결과를 커밋 메시지나 리포트에 남긴다. 후보 출처: 3단계 norm_rules 출처 공지들, 멘토가 링크한
  110 정부민원안내 전자고지 안내·국세청 모바일 안내문.
- `origin: synthetic` — 모자란 칸만. 후보 목록을 사용자에게 보여 주고 **승인받은 것만** 추가한다. `source: null`.
- 실제 기관명·전화번호·도메인은 공식 출처에 있는 것만 쓴다. 개인 이름·번호는 자리표시자(`<이름>`)로 둔다.

### 3.3 정답 라벨 칸

| 칸 | 값 | 붙이는 행 |
|---|---|---|
| `kind` | `official_notice` / `same_pretext` / `unclear_org` | 새 36건 |
| `expected_business` | `delivery` / `penalty` / `overseas_payment` / `none` | **모든 행** |
| `expected_actions` | `MessageDoubt` 값 목록(`unknown` 제외, 빈 목록 허용) | 새 36건 |

기존 622건의 `expected_business`:

- `category` 가 `delivery`·`address_correction` → `delivery`, `penalty` → `penalty`, 그 밖 → `none`.
- `payment` 는 해외결제와 다른 결제가 섞여 있다. "해외·국외·국제·직구·해외승인" 단어로 1차 분류하고 사람이
  한 건씩 확인한다. 애매한 행은 사용자에게 목록으로 보여 주고 정한다.

## 4. 평가 스크립트

### 4.1 `ai/eval/guidance_eval.py` (새 파일)

1. 각 행의 `message` 를 고정 규칙으로 분석한다: `classify_topic(message)`, `identify_brand(message, None)`
   (`unknown` → 없음), 요구 행동은 `ai.taxonomy` 의 고정 규칙 후보.
2. 결과를 `AnalysisResponse` 모양 dict 로 만들어 `find_guidance()` 에 넣는다(`url.official` = `not_registered`,
   `env.answer` = `not_run`).
3. 두 모드: **현재**(실제 `norm_rules`, curated 만) / **전체 가정**(`match` 가 있는 draft 도 curated 로 본 사본).
4. 반환된 안내의 업무는 `rule_id` 로 norm_rules 파일의 `business` 를 찾아 정한다.

안내 결과 분류 (행마다 하나):

| 분류 | 조건 |
|---|---|
| 맞음 | `expected_business` ≠ none, 안내가 있고 모든 안내의 업무 = `expected_business` |
| 빠짐 | `expected_business` ≠ none, 안내 없음 |
| 엉뚱함 | 안내 중 하나라도 업무 ≠ `expected_business` (`expected_business` = none 인데 안내가 있으면 포함) |
| 맞게 없음 | `expected_business` = none, 안내 없음 |

### 4.2 리포트 `ai/eval/reports/<날짜>-guidance.md`

| 표 | 내용 |
|---|---|
| ① 확인 경로 적절성 | 모드별, 정답 업무별·종류별 맞음/빠짐/엉뚱함/맞게 없음 개수와 비율 |
| ② 업무 인식 | 정답 업무 × 주제 분포. 택배 행의 택배사 인식(5개사 / unknown / 기타 업체) |
| ③ 요구 행동 정확도 | 새 36건: 정답 대비 맞게 찾음·놓침·잘못 찾음 (행 단위 합계) |
| ④ 정상 문자 안내 | benign·hard_negative 행에 `procedure` 안내가 붙은 비율과 예시(최대 5건) |
| ⑤ 결정용 요약 | 해외결제·과태료 행의 빠짐 비율, 택배 행 중 택배사 unknown 수(예: "[우체국]"), 전체 가정 모드에서 NR-0019 가 붙는 행 |

리포트 머리에 커밋 해시, 데이터 행 수, 한계(고정 규칙만)를 적는다.

### 4.3 `ai/eval/rag_eval.py` 변경

- 새 `kind` 별 표를 추가한다(스미싱 hit@3, 정상 부착률). 기존 표와 계산은 바꾸지 않는다.
- 새 36건이 들어가면 전체 수치가 바뀐다. 추가 후 TF-IDF 기준 리포트를 새로 내고 이전 리포트는 기록으로 둔다.
  5단계 임베딩 비교는 새 기준으로 한다.

## 5. 테스트

- `ai/tests/test_guidance_eval.py`: 안내 결과 분류(맞음·빠짐·엉뚱함·맞게 없음)를 작은 예시로 검사. 업무 매핑이
  norm_rules 의 `business` 를 따르는지.
- `ai/tests/test_kb_data.py`:
  - `expected_business` 가 모든 행에 있고 허용 값인지.
  - `kind` 가 있으면 허용 값이고 `expected_actions` 가 `MessageDoubt` 값 목록인지.
  - `origin` 허용 값에 `official_example`·`synthetic` 추가. `official_example` 이면 `source` 필수, `synthetic` 이면
    `template` 없이 허용(지금 검사는 web_public 이 아니면 `synthetic_template`+`template` 을 요구한다).
  - 새 행도 기존 검사(주소 없는 `message`, 개인정보, KB 와 겹침 없음, 분할 단위)를 통과.

## 6. 이번에 하지 않는 것

- 주제(`Topic`) 추가, NR-0019 조건 변경, 택배사 인식(`[우체국]`) 보완 — 리포트 ⑤를 보고 사용자와 정한 뒤 따로.
- 임베딩 비교(로드맵 5단계).
- LLM 을 쓰는 평가(AI 추출 요구 행동·택배사 포함).
