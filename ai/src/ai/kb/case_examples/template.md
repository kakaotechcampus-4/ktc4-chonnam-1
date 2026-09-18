---
id: CE-0000
status: draft
origin: team_collected
collected_at: 2026-01-01
reviewer: 미지정
normalized: ""
variants:
  - ""
categories: []
claimed_brand: ""
---

# 작성 규칙

- `status: curated` 인 레코드만 검색에 쓰입니다. 사람 검토 전에는 `draft` 로 둡니다.
- `variants` 에는 원문을 그대로 넣습니다. 정규화 후 같은 문장은 한 레코드에 모읍니다.
- `normalized` 는 `ai.kb.normalize.normalize()` 결과입니다.
- 개인정보를 제거하고 평가셋과 중복되지 않는지 확인합니다.
- URL은 넣지 않습니다. 검색은 링크를 제외한 본문만 씁니다.
- 스크립트로 일괄 등재한 레코드는 `reviewer: bulk-import-<날짜>` 로 표시한다. 문면이
  수집 원문이라는 것만 보장하며 건별 분류·요약은 검토되지 않았다는 뜻이다.
- 일괄 등재분은 검색 인덱스에는 들어가지만 `categories` 와 `claimed_brand` 를 비워 둔다.
  사람이 검토하면서 채운다.
