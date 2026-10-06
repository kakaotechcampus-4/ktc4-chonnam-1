# 평가 데이터셋

| 파일 | 내용 |
|---|---|
| `smishing.jsonl` | 팀이 수집한 실물 스미싱. KB에 등재하지 않은 분량 |
| `benign.jsonl` | 실제 택배사·쇼핑몰 정상 알림 |

각 줄은 `{"text": "...", "label": "smishing" | "benign"}` 이다. `benign.jsonl` 에는
검색용 `message` 도 있다(아래 [`text` 와 `message`](#text-와-message) 참조).

KB(`ai/src/ai/kb/case_examples/`)와 겹치지 않게 유지한다. 겹치면 자기 자신을
검색해 유사도 1.0이 나온다. `test_eval_dataset_is_disjoint_from_kb` 가 검사한다.

개인정보는 커밋 전에 마스킹한다. 이 저장소는 public이다. 자리표시자로 치환하되
필드 라벨은 남긴다. 값이 구체적으로 존재하는지가 정상/스미싱 판별 신호이므로
필드를 통째로 지우면 두 집합의 차이가 사라진다.

측정 항목은 오탐률, 누락률, 추출 항목별 근거 정확도, 유사도 분포 겹침이다.

## `rag_testset.jsonl` — RAG 검색 정확도 테스트셋

`CategoryCode` 14개 유형(`other`·`unknown` 제외) × 세 그룹.

```json
{"id": "S-penalty-01", "group": "smishing" | "benign" | "hard_negative",
 "category": "<CategoryCode>", "text": "...", "message": "...",
 "origin": "web_public" | "synthetic_template", "source": "<출처 URL>" | null,
 "template": null | "<템플릿 ID>", "split": "dev" | "test",
 "exclude": "<평가에서 뺀 사유>"}   // exclude 는 판단을 보류한 행에만 있다
```

| group | 뜻 | 출처 | 건수 |
|---|---|---|---|
| `smishing` | 실물 스미싱 | 공개 웹 원문 (`source` 필수) | 62 (원래 215, 아래 분할 참조) |
| `benign` | 평범한 정상 문자 | 공식 알림 양식 기반 **합성** | 280 (유형별 20) |
| `hard_negative` | 정상이지만 스미싱과 겉모양이 비슷한 문자 (링크·금액·기한·"본인 아닐 시" 등) | 공식 알림 양식 기반 **합성** | 280 (유형별 20) |

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

주소로 보고 지운 것 (2026-10-07, `rag_testset.jsonl` 78행 · `benign.jsonl` 4행):

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
- `L.POINT` 는 주소가 아니다. `.point` 는 최상위 도메인 목록(Public Suffix List)에 없다.
- 주소를 지운 자리의 빈 괄호 `()` 와 겹친 공백만 정리했다. 그 밖의 글자는 바꾸지 않았다.
  주소가 없던 행은 `message == text` 다.
- `test_search_datasets_carry_address_free_message` 가 `message` 에 주소가 남지 않았는지 검사한다.
  행을 추가할 때도 `message` 를 같은 규칙으로 채운다.

## 현재 한계

`benign.jsonl` 은 5건뿐이다. 목표는 20~30건이다. **n=5 로 측정한 오탐률은
의미가 없다** — 한 건만 틀려도 20%p 가 움직인다. 정상 알림을 더 모아
채우기 전까지 오탐 수치를 품질 근거로 인용하지 않는다.

스미싱 쪽은 수백 건을 확보해 KB 와 평가셋으로 나눴다. 불균형이 크다는 점도
측정 해석에 반영한다.
