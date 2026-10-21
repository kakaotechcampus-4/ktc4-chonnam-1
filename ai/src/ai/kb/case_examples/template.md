---
id: CE-0000
status: draft
origin: web_public
source: ""
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

## 출처 규칙 (2026-10-07 부터 추가하는 레코드)

`origin: team_collected` 는 2026-09-18 일괄 등재분에만 쓴 값이다. 크롤링분과 팀원 수신분이
섞여 구분되지 않는다(`../README.md` 의 "출처 현황"). 새 레코드는 아래 둘 중 하나로 적는다.

- **인터넷에서 가져온 문자**: `origin: web_public`, `source: "<가져온 페이지 링크>"` 필수.
  게시한 곳이 스미싱이라고 밝힌 글이어야 한다(보안 업체·언론·기관 공지 등).
- **팀원이 직접 받은 문자**: `origin: team_received`, `received_at: <받은 날짜>`,
  `label_basis: "<스미싱으로 본 근거>"` 필수. 근거는 받은 사람의 느낌이 아니라 확인한 사실로
  적는다(예: 링크가 악성으로 확인됨, 공개된 사기 사례와 같은 문구). 이름·주소·번호 등은
  가린 뒤 넣는다.
- 근거나 출처를 적을 수 없으면 `status: draft` 로 둔다. draft 는 검색에 쓰이지 않는다.
  제보됐다는 이유만으로 스미싱 라벨을 붙이지 않는다.
