# kakao-templates

챗봇 응답 카드 템플릿 (선언형). FE 소유.
렌더러는 `backend/src/server/templates/`.

카카오 응답 타입: simpleText / basicCard / itemCard / listCard / quickReplies

## 카드 JSON

`cards/`에 카드 JSON을 둔다.

현재 BE 형식(`backend/src/server/templates/kakao_templates/`) 기준. BE 연결 시 바뀔 수 있음.

- 파일 이름은 `화면코드-이름.json` (예: `r1-lookalike.json`, `w1-welcome.json`). 화면 코드는
  `docs/designs/chatbot-copy.md` 3절과 같다. BE는 확장자 뺀 이름으로 찾는다.
- 내용은 카드 하나가 아니라 카카오 스킬 응답 전체 (`{"version": "2.0", "template": {"outputs": [...]}}`).
- 바뀌는 값은 `{{ image_url }}`처럼 중괄호 두 개로 표시한다.
- UTF-8로 저장한다.
- 문구 원문은 `docs/designs/chatbot-copy.md`다. 문구를 바꾸면 두 곳을 함께 고친다.

### 채워야 하는 값

| 값 | 쓰는 카드 | 채우는 주체 |
| --- | --- | --- |
| `block_b1` `block_b1a` `block_b2a` `block_b2b` `block_w2` `block_w2a` `block_r1a` `block_detail` | 버튼 `blockId` | 관리자센터 블록 ID. 블록을 만든 뒤 채운다 |
| `block_consent` | W3 `동의하고 확인하기` | 동의 기록 후 보류한 문자로 분석을 잇는 스킬 블록 (BE 미구현) |
| `block_retry` | (지금 안 씀) R4 `다시 시도` | 재검사 미구현이라 R4에서 버튼을 뺐다. 구현하면 되살린다 |
| `kisa_chatbot_url` `kisa_chat_url` | B2b·R1a 상담 버튼 | KISA 인계 방법 확인 후 |
| `org_name` `official_url` | R2 · R8(`org_name`만) | `org_name`은 `message.brand`. `official_url`은 결과에 없다. 브랜드별 공식 홈페이지 주소를 BE 목록(`services/official_domains.py`, 지금은 도메인만 있음)에 더해야 한다. 문자 속 URL은 쓰지 않는다 |
| `signal_title` `observed` `reason` `unverified` | 자세히 보기 캐러셀 | 결과의 `details.signals`(검증된 근거)만. 채우는 규칙과 신호별 문구는 `docs/designs/chatbot-copy.md` 4절 "빈칸 채우는 규칙". 128자 안 |
| `used_info` `sent_info` `retention` `sent_items` `external_service` | W2a·W3 | 개인정보 정책 확인 후. 확정 전 배포 금지 |

- 이미지 주소가 아직 없으므로 모든 카드를 썸네일 없는 `textCard`로 두었다(`chatbot-flow.md` 9절 (3)).
  이미지가 준비되면 해당 카드를 `basicCard` + `thumbnail`로 바꾼다.
- R5(`r5-analyzing.json`)는 콜백 대기 응답이라 `template` 없이 `useCallback`과 `data.text`만 있다.
  버튼을 달 수 없다.
- K2(시각 + 저장한 결과 카드)와 K3(R4 재사용)는 BE가 조립하는 응답이라 파일로 두지 않는다.

## 프로토타입 자산

`assets/prototype/`에는 개발 채널의 네이티브 카드 검증에 사용한 800×400 이미지가 있다.
운영 자산이 아니며 사용자 테스트 후 교체하거나 제거할 수 있다. 이미지에는 동적 문구·브랜드·URL을
넣지 않고, 실제 제목과 설명은 카카오 카드의 텍스트 필드에서 렌더링한다.
