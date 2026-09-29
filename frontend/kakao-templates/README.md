# kakao-templates

챗봇 응답 카드 템플릿 (선언형). FE 소유.
렌더러는 `backend/src/server/templates/`.

카카오 응답 타입: simpleText / basicCard / itemCard / listCard / quickReplies

## 카드 JSON

`cards/`에 카드 JSON을 둔다.

현재 BE 형식(`backend/src/server/templates/kakao_templates/`) 기준. BE 연결 시 바뀔 수 있음.

- 파일 이름은 `r번호-이름.json` (예: `r1-lookalike.json`). BE는 확장자 뺀 이름으로 찾는다.
- 내용은 카드 하나가 아니라 카카오 스킬 응답 전체 (`{"version": "2.0", "template": {"outputs": [...]}}`).
- 바뀌는 값은 `{{ image_url }}`처럼 중괄호 두 개로 표시한다.
- UTF-8로 저장한다.

## 프로토타입 자산

`assets/prototype/`에는 개발 채널의 네이티브 카드 검증에 사용한 800×400 이미지가 있다.
운영 자산이 아니며 사용자 테스트 후 교체하거나 제거할 수 있다. 이미지에는 동적 문구·브랜드·URL을
넣지 않고, 실제 제목과 설명은 카카오 카드의 텍스트 필드에서 렌더링한다.
