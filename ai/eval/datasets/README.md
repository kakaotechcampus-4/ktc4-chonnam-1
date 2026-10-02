# 평가 데이터셋

| 파일 | 내용 |
|---|---|
| `smishing.jsonl` | 팀이 수집한 실물 스미싱. KB에 등재하지 않은 분량 |
| `benign.jsonl` | 실제 택배사·쇼핑몰 정상 알림 |

각 줄은 `{"text": "...", "label": "smishing" | "benign"}` 이다.

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
 "category": "<CategoryCode>", "text": "...",
 "origin": "web_public" | "synthetic_template", "source": "<출처 URL>" | null,
 "template": null | "<템플릿 ID>"}
```

| group | 뜻 | 출처 | 건수 |
|---|---|---|---|
| `smishing` | 실물 스미싱 | 공개 웹 원문 (`source` 필수) | 215 |
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

## 현재 한계

`benign.jsonl` 은 5건뿐이다. 목표는 20~30건이다. **n=5 로 측정한 오탐률은
의미가 없다** — 한 건만 틀려도 20%p 가 움직인다. 정상 알림을 더 모아
채우기 전까지 오탐 수치를 품질 근거로 인용하지 않는다.

스미싱 쪽은 수백 건을 확보해 KB 와 평가셋으로 나눴다. 불균형이 크다는 점도
측정 해석에 반영한다.
