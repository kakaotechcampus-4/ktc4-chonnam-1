# 평가 데이터셋

| 파일 | 내용 |
|---|---|
| `smishing.jsonl` | 실물 스미싱. KB `team_collected` 와 같은 원본에서 KB에 등재하지 않은 분량. 인터넷 크롤링분과 팀원 수신분이 섞여 건별 구분·출처 링크 없음(`ai/src/ai/kb/README.md` 의 "출처 현황") |
| `benign.jsonl` | 정상 알림 문자. 택배·쇼핑몰 실물 5건 + 금융·공공기관·통신·보험 등 합성 16건(2026-10-09 추가, 아래 "현재 한계" 참고) |

각 줄은 `{"text": "...", "label": "smishing" | "benign"}` 이다. `benign.jsonl` 에는
검색용 `message` 도 있다(아래 [`text` 와 `message`](#text-와-message) 참조).

KB(`ai/src/ai/kb/case_examples/`)와 겹치지 않게 유지한다. 겹치면 자기 자신을
검색해 유사도 1.0이 나온다. `test_eval_dataset_is_disjoint_from_kb` 가 검사한다.

개인정보는 커밋 전에 마스킹한다. 이 저장소는 public이다. 자리표시자로 치환하되
필드 라벨은 남긴다. 값이 구체적으로 존재하는지가 정상/스미싱 판별 신호이므로
필드를 통째로 지우면 두 집합의 차이가 사라진다.

측정 항목은 오탐률, 누락률, 추출 항목별 근거 정확도, 유사도 분포 겹침이다.

## `rag_testset.jsonl` — RAG 검색 정확도 테스트셋

`CategoryCode` 14개 유형(`other`·`unknown` 제외) × 세 그룹. 여기에 새 문자 종류(`kind`) 36건을 더했다.

```json
{"id": "S-penalty-01", "group": "smishing" | "benign" | "hard_negative",
 "category": "<CategoryCode>", "text": "...", "message": "...",
 "origin": "web_public" | "synthetic_template" | "official_example" | "synthetic", "source": "<출처 URL>" | null,
 "template": null | "<템플릿 ID>", "split": "dev" | "test",
 "expected_business": "delivery" | "penalty" | "overseas_payment" | "none",
 "kind": "official_notice" | "same_pretext" | "unclear_org",   // 새 행에만
 "expected_actions": ["<MessageDoubt 값>", ...],                // 새 행에만
 "near_dup_of": "<CE-번호>",       // 아는 캠페인인 스미싱 행에만 있다
 "exclude": "<평가에서 뺀 사유>"}   // exclude 는 판단을 보류한 행에만 있다
```

| group | 뜻 | 출처 | 건수 |
|---|---|---|---|
| `smishing` | 실물 스미싱 | 공개 웹 원문 (`source` 필수) | 62 (원래 215, 아래 분할 참조) + 새 문자 18 |
| `benign` | 평범한 정상 문자 | 공식 알림 양식 기반 **합성** | 280 (유형별 20) + 새 문자 18 |
| `hard_negative` | 정상이지만 스미싱과 겉모양이 비슷한 문자 (링크·금액·기한·"본인 아닐 시" 등) | 공식 알림 양식 기반 **합성** | 280 (유형별 20) |

### 새 문자 종류 (`kind`, 2026-10-11)

PR #39 멘토 피드백의 세 종류를 업무(택배·과태료·해외결제)마다 4건씩 넣었다.

| kind | 뜻 | 그룹 |
|---|---|---|
| `official_notice` | 정상 전자고지 | benign |
| `same_pretext` | 명분은 같고 요구 행동이 다른 문자 | smishing 2 + benign 2 |
| `unclear_org` | 기관·관할을 알기 어려운 문자 | smishing |

공식 출처 예시(`origin: official_example`) 6건을 먼저 넣고, 빈 칸 30건은 사용자가 검수한 합성 문자(`origin: synthetic`, 2026-10-13)로 채웠다.
합성 문자는 실물이 아니므로 이 칸의 수치를 실제 성능으로 인용하지 않는다.
정상 문자는 실제 기관·카드사 이름과 공식 주소를 쓰고, 사기 문자의 링크·번호는 무력화(`hxxps`, `[.]`)하고 일부를 가렸다.

- 스미싱 원문 대부분은 이스트시큐리티 알약 블로그 게시물 제목·월간 트렌드 보고서이며,
  일부는 언론·정부 보도자료다. URL 은 출처에서 이미 가려진(`hxxp`, `***`) 형태다.
- 스미싱은 유형별 목표 20건을 다 채우지 못했다. 공개 원문이 적은 유형:
  `public_refund` 5 · `telecom_refund` 7 · `obituary` 9 · `acquaintance_impersonation` 9 ·
  `prize_or_event` 15 · `public_support` 14 · `account_security` 16. 합성으로 채우지 않았다.
- 유형은 키워드로 1차 분류한 뒤 사람이 검토했다. 한 문자가 여러 유형에 걸치면(예: 해외결제 + 카드) 대표 유형 하나만 붙였다.
- **합성 문자는 실물이 아니다.** 실제 정상 문자 분포와 다를 수 있으므로 이 셋으로 측정한
  오탐률을 실제 성능으로 인용하지 않는다. 팀 실물로 교체할 때는 같은 `id` 를 유지하고
  `origin`·`source` 를 바꾼다. 같은 `template` 에서 나온 행은 서로 거의 중복이다.
- KB 와 겹치는 원문 38건은 제외했다(`test_eval_dataset_is_disjoint_from_kb`).

### 정답 업무 (`expected_business`)

안내 평가(`eval/guidance_eval.py`)의 정답이다. `category` 가 `delivery`·`address_correction` 이면 `delivery`,
`penalty` 면 `penalty`, `payment` 는 해외결제·해외 카드 승인 문자면 `overseas_payment`, 결제는 국내인데
배송이 주제면(예: `[해외배송] … 결제완료`) `delivery`, 아니면 `none`. 그 밖은 `none`.
`payment` 는 단어 규칙으로 1차 분류한 뒤 사람이 한 건씩 확인했다(2026-10-11, 애매한 4건은 사용자가 확정).
카드 승인 알림("[OO카드] … 승인")은 국내 결제여도 `overseas_payment`(해외결제·구매 승인 업무)다. `payment`·`card_or_account`
7건을 바꿨다(2026-10-14, 사용자 결정). 휴대폰·통신사 소액결제 승인과 카드 이상거래 정지 알림은 `none` 그대로다.

### KB·평가 분할 (2026-10-03)

스미싱 215건을 유형별로 KB : dev : test = 5 : 1 : 1 로 나눴다
([설계](../../../docs/superpowers/specs/2026-10-03-rag-kb-eval-split-design.md)).

- KB 몫 153건 중 147건은 링크 표기를 지워 KB 레코드(`CE-0298`~`CE-0444`)로 옮겼다. 레코드의 `testset_id` 가 원래 id 다.
- 나머지 6건은 어느 쪽에도 넣지 않았다. 1건은 링크를 지우자 다른 행과 같아졌고, 5건은 `smishing.jsonl` 에 같은 문장이 있다.
  KB 는 어느 평가셋과도 겹치면 안 된다.
- 남은 행에 `split` 을 붙였다. 스미싱은 출처(`source`) 단위로 묶어 배정했고, 정상·hard negative 는
  기존 템플릿 해시 배정을 그대로 적었다.
- 평가 몫은 앞으로도 KB 에 넣지 않는다.

## `text` 와 `message`

운영에서 AI 는 BE 가 주소를 지운 본문만 받는다(`main.py` 의 `split_message()` →
`analyze_message_part(message)`). 평가도 같은 형태로 재야 하므로 검색에는 `message` 를 쓴다.

- `text`: 출처 원문 그대로. 출처 대조용이다.
- `message`: `text` 에서 주소를 지운 본문. `rag_eval.py` 와 데이터 테스트가 이것으로 검색한다.

주소로 보고 지운 것 (2026-10-07, `rag_testset.jsonl` 78행 · `benign.jsonl` 4행.
2026-10-11 에 `benign.jsonl` 합성 16행의 `https://` 주소도 같은 규칙으로 지웠다):

| 형태 | 예 |
|---|---|
| 스킴이 있는 주소 | `https://www.cjlogistics.com/...`, `https://<병원_도메인>/result` |
| 출처에서 가린 주소 | `hxxps://g**[.]su/dP****t`, `hxxtp://l****le.com/l**/`, `0*[.]ks/0***4` |
| 스킴 없는 주소 | `li**.cc/H***`, `infos-a*****ts.com`, `위택스(www.wetax.go.kr)` → `위택스` |

- **운영과 다른 점.** 지금 BE 의 `split_message()` 는 `http(s)://` 로 시작하는 주소만 지운다.
  스킴 없는 주소(`www.wetax.go.kr`, `li**.cc/H***`)는 운영에서는 본문에 남는다. 이 평가는 BE 가
  주소를 모두 지운다고 가정하고 이것도 지웠다. 주소 인식 범위는 BE 와 확인 중이다.
- 출처에서 가린 주소(`hxxps`, `[.]`)는 원래 `https://` 주소였으므로 운영에서도 지워질 주소로 본다.
- **판단을 보류한 행.** `SSG.COM`·`APPLE.COM` 은 실제 도메인이면서 문장 안에서는 발신자·가맹점
  이름이다(`[SSG.COM] 주문하신…`). BE 가 지울지 정해지지 않아 `message` 에 남겨 두고, 그 5행에
  `"exclude": "<사유>"` 를 붙여 평가에서 뺐다. BE 주소 인식 범위가 정해지면 규칙에 맞춰
  `message` 를 고치고 `exclude` 를 지운다.
- 공식 출처 예시(`official_example`)·합성(`synthetic`) 행의 사기 링크는 `text` 에도 주소를 남기지 않고 `<링크>` 로 적는다.
  `message` 에서는 `<링크>` 와 그 앞 구분 기호(`:`)를 지운다.
- `L.POINT` 는 주소가 아니다. `.point` 는 최상위 도메인 목록(Public Suffix List)에 없다.
- 주소를 지운 자리의 빈 괄호 `()` 와 겹친 공백만 정리했다. 그 밖의 글자는 바꾸지 않았다.
  주소가 없던 행은 `message == text` 다.
- `test_search_datasets_carry_address_free_message` 가 `message` 에 주소가 남지 않았는지 검사한다.
  행을 추가할 때도 `message` 를 같은 규칙으로 채운다.

## 아는 캠페인 (`near_dup_of`)

KB 에 거의 같은 사례가 있는 스미싱 행에 그 사례 번호를 적는다. 평가는 스미싱 점수를
"아는 캠페인" 과 "새 캠페인" 으로 나눠 낸다. 행을 KB 로 옮기거나 빼지 않는다.

- **왜.** 문자 n-gram 검색은 숫자·이름만 바뀐 변형은 찾지만 같은 뜻을 다른 표현으로 쓰면
  거의 못 찾는다. 섞은 평균은 그 차이를 가리고, 검색 방식을 바꿨을 때 어디서 나아졌는지도 가린다.
- **기준.** 같은 틀의 문장이고 다른 점이 숫자·날짜·사람·발신자·기관명, 또는 `바로`/`즉시` 같은
  작은 단어 하나뿐이면 거의 같다고 본다. 본문 내용이나 요구 행동이 다르면 다른 캠페인이다
  (예: `배송불가(도로명불일치)` 가 같아도 `변경요망` / `앱 다운로드` 면 다름).
- **절차.** `message` 로 KB 를 검색해 top-2 유사도 0.3 이상인 스미싱 행을 후보로 뽑고, 사람이
  하나씩 판정했다(2026-10-07, 후보 dev 10행 · test 8행 → 표시 dev 7행 · test 7행). 유사도 숫자로
  자르지 않는다. 0.35 인데 같은 캠페인도, 0.37 인데 다른 캠페인도 있었다.
- 정상·hard negative 는 표시하지 않는다. KB 사례와 닮게 만든 의도된 시험이다.
- 행이나 KB 사례를 추가하면 같은 절차로 후보를 다시 본다.

## 현재 한계

`benign.jsonl` 은 2026-10-09 기준 21건이다(목표 20~30건 충족). 다만 그중
**5건만 실물**(택배·쇼핑몰)이고, 나머지 16건은 멘토 리뷰(PR #54: "기관명,
납부기한, 링크가 들어 있는 정상 문자도 꼭 넣어 주세요")에 대응해 추가한
**합성** 문자다 — 카드사·은행·공공기관(건강보험·국세청)·통신사·보험사·
전기/수도 요금·렌탈료 등 "기한 + 공식 링크"가 함께 나오는 정상 알림이
스미싱으로 오판되는지 보려는 목적이다. 공식 도메인은 실제 기관의 진짜
도메인을 썼지만, 문구 자체는 실물 수집이 아니라 일반적인 고지 양식을
본떠 작성했다. 실제 정상 문자 분포와 다를 수 있으므로 이 합성분으로 측정한
오탐률을 실제 성능으로 인용하지 않는다 — `rag_testset.jsonl`의 `benign`/
`hard_negative`와 같은 주의가 적용된다. 실물 정상 알림을 더 모으면 같은
기준으로 교체한다.

스미싱 쪽은 수백 건을 확보해 KB 와 평가셋으로 나눴다. 불균형이 크다는 점도
측정 해석에 반영한다.
