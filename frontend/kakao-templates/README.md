# kakao-templates

챗봇 응답 카드 템플릿 (선언형). FE 소유.
렌더러는 `services/kakao_card_renderer.py`. BE가 이 폴더의 `cards/`를 직접 읽는다.
`backend/src/server/templates/kakao_templates/`는 시험 경로(`/test/kakao/r1-lookalike`)만 쓰는 옛 사본이라 기준이 아니다.

카카오 응답 타입: simpleText / basicCard / itemCard / listCard / quickReplies

## 카드 JSON

`cards/`에 카드 JSON을 둔다.

- 파일 이름은 `화면코드-이름.json` (예: `r1-lookalike.json`, `w1-welcome.json`). 화면 코드는
  `docs/designs/chatbot-copy.md` 3절과 같다. BE는 확장자 뺀 이름으로 찾는다.
- 내용은 카드 하나가 아니라 카카오 스킬 응답 전체 (`{"version": "2.0", "template": {"outputs": [...]}}`).
- 바뀌는 값은 `{{ image_url }}`처럼 중괄호 두 개로 표시한다.
- UTF-8로 저장한다.
- 문구 원문은 `docs/designs/chatbot-copy.md`다. 문구를 바꾸면 두 곳을 함께 고친다.

### 채워야 하는 값

| 값 | 쓰는 카드 | 채우는 주체 |
| --- | --- | --- |
| `block_b1` `block_b1a` `block_b2` `block_b2a` `block_b2b` `block_w2` `block_w2a` `block_detail` | 버튼 `blockId` | **채움** (2026-10-01). 아래 "블록 ID" 표 |
| `block_consent` | W3 `동의하고 확인하기` | 동의 기록 후 보류한 문자로 분석을 잇는 스킬 블록 (BE 미구현) |
| `block_detail` | R1·R9 `자세히 보기` | 2026-10-14 되살림(이슈 #69). BE가 버튼의 `extra.job_id`를 채우고, 이 블록의 스킬이 `/kakao/skill`을 부르면 저장한 결과로 캐러셀을 보낸다. 관리자센터 스킬 연결은 확인 필요 |
| `block_retry` | (지금 안 씀) R4 `다시 시도` | 재검사 미구현이라 R4에서 버튼을 뺐다. 구현하면 되살린다 |
| (KISA 상담 주소) | B2a·B2b·C3 `KISA 상담하기` | **채움** (2026-10-27). 빈칸이던 `kisa_chatbot_url`·`kisa_chat_url`을 `https://www.kisa.or.kr/118`로 바꿨다. 관리자센터 카드에 넣은 값과 같다. B2b의 `챗봇 상담`·`채팅 상담`은 같은 페이지를 열어 `KISA 상담하기` 하나로 합쳤다. 새 A2·K6 카드도 같은 주소를 쓴다 |
| `org_name` `official_url` | R2 · R8(`org_name`만) | `org_name`은 `message.brand`. `official_url`은 결과에 없다. 브랜드별 공식 홈페이지 주소를 BE 목록(`services/official_domains.py`, 지금은 도메인만 있음)에 더해야 한다. 문자 속 URL은 쓰지 않는다 |
| `signal_title` `observed` `reason` `unverified` | 자세히 보기 캐러셀 | 결과의 `details.signals`(검증된 근거)만. 채우는 규칙과 신호별 문구는 `docs/designs/chatbot-copy.md` 4절 "빈칸 채우는 규칙". 128자 안 |
| `headline` `action_guide` `message_claims` `checked` `unchecked` | R9 | BE가 분석 결과로 채운다. 규칙은 `docs/designs/chatbot-copy.md` 3절 R9 "빈칸 채우는 규칙". 문자에서 의심 정황(민감한 요구 행동)을 확인하면 `headline`·`action_guide`가 주의 갈래로 바뀐다(PR #86 멘토 리뷰). `message_claims`는 칸 제목까지 포함하고, 줄이 없으면 빈 문자열이다. 이전 `org_label`은 `action_guide` 안으로, `message_reason`은 `message_claims`로 합쳤다. 채우기 전에 배포하면 빈칸이 글자 그대로 보인다. **지금 `r9-uncertain.json`은 옛 빈칸(`org_label`·`checked`·`unchecked`·`message_reason`) 버전이다**(2026-10-27 `73d3fc6`). BE가 렌더러를 구현할 때 `git revert 73d3fc6`으로 새 빈칸 버전을 되살린다 |
| `guidance_text` | 자세히 보기 공식 확인 방법(C4) | BE가 `find_guidance()` 결과로 채운다(이슈 #69). 규칙은 `docs/designs/chatbot-copy.md` 4절 C4 |
| `sent_items` `external_service` | W3 | 개인정보 정책 확인 후. 확정 전 배포 금지 (W2a는 2026-10-14부터 빈칸 없는 시험 운영 문구) |

### 블록 ID

카드 JSON에는 아래 ID가 들어 있다. 2026-10-01 관리자센터에서 받은 값이다.
관리자센터 주소창의 `?scenarioId=…`는 버튼에 쓰지 않으므로 뺐다.
블록을 지우고 다시 만들면 ID가 바뀌니, 그때는 이 표와 카드 JSON을 같이 고친다.

| 자리 | 블록 | 블록 ID |
| --- | --- | --- |
| `block_b1` | 스미싱 확인 (붙여넣기 안내) | `6aa6a4436d537a3eabb81ad5` |
| `block_b1a` | 문자 복사하는 법 | `6aad62fc707aa28d466cd3a1` |
| `block_b2` | 신고 상담 | `6aad5cc1d29c9829bcc2adb6` (2026-10-05) |
| `block_b2a` | 신고 > 문자만 받았어요 | `6ab78d64322b97a3a1cc9567` |
| `block_b2b` | 신고 > 링크를 눌렀어요 | `6ab78d883dd52f826f111e49` |
| `block_w2` | 개인정보 처리 안내 | `6aad5d0387c6230d77feeda2` |
| `block_w2a` | 개인정보 처리 상세 안내 | `6abe5d6a5402049514861b78` |
| (`block_r1a`) | 신고 행동 안내 ("지금 이렇게 하세요") | `6abba220c841065f2fe49628` — 2026-10-27 카드에서 뺐다(`chatbot-flow.md` 11절). 대화방에 남은 옛 R1 카드가 아직 부르므로 관리자센터 블록은 시험 운영이 끝난 뒤 지운다 |
| `block_detail` | 자세히 보기 (스킬 블록) | `6aad3abfe745d3358bd90c8d` |

- 이미지가 없는 카드는 썸네일 없는 `textCard`로 둔다(`chatbot-flow.md` 9절 (3)).
  이미지가 준비되면 해당 카드를 `basicCard` + `thumbnail`로 바꾼다.
- 확정 카드 이미지는 `assets/cards/`에 둔다. `imageUrl`은 `develop` 브랜치의
  `raw.githubusercontent.com` 주소라 **develop에 머지된 뒤에야 보인다.**
  지금 적용한 카드: W1, B1a, B2, B2a, B2b, R1~R4, 자세히 보기 캐러셀(C1~C3).
  이미지 파일 이름은 카드 파일 이름을 따른다(R1만 `r1-danger.png`, 캐러셀은 `c1-signal` `c2-scope` `c3-action`).
  B1은 W1 이미지(`w1-welcome.png`)를 같이 쓴다.
- R5는 `template`이 없어 이미지를 붙일 수 없으므로 그대로 둔다. R5용으로 받은 "분석 중"
  이미지는 K1(`k1-still-running.png`)에 쓴다.
- R5(`r5-analyzing.json`)는 콜백 대기 응답이라 `template` 없이 `useCallback`과 `data.text`만 있다.
  버튼을 달 수 없다.
- K2(시각 + 저장한 결과 카드)와 K3(R4 재사용)는 BE가 조립하는 응답이라 파일로 두지 않는다.

## 최소 스미싱 시나리오의 관리자센터 연결

기존 카드의 모양·이미지·문구·버튼은 유지한다. R1·R9에는 이미 자세히 보기 버튼이 있고,
BE는 `extra.job_id`를 채워 저장된 분석 결과를 조회한다. R8에는 버튼을 추가하지 않는다.

아래는 필요한 연결 조건이며, 현재 관리자센터에 적용됐다는 뜻은 아니다. Chrome으로
설정을 변경하기 전 대상과 변경안을 보고하고 사용자 승인을 받는다.

- 문자 입력을 받는 블록과 자세히 보기 블록은 같은 `/kakao/skill`을 호출한다.
- 자세히 보기 블록 ID는 `6aad3abfe745d3358bd90c8d`이며 스킬 응답을 사용한다.
- 결과 확인 블록의 `intent.name`은 `결과 확인`이어야 한다. `결과 확인`·`결과 확인하기` 발화를 확인한다.
- 입력 분석은 콜백을 사용한다. 결과 확인과 자세히 보기는 저장된 결과로 즉시 응답한다.
- 스킬 요청의 `X-Skill-Secret`은 Render 설정과 일치해야 한다. 실제 비밀값은 문서에 적지 않는다.

연결 테스트: 저장소 루트에서 `python -X utf8 -m pytest tests/test_smishing_card_flow.py -q`.
R1·R9의 실제 버튼 `extra`로 스킬을 호출해 저장된 상세를 받는지 확인한다.
카드 표시 방식이나 실제 휴대폰 화면의 검증을 대신하지 않는다.

## 프로토타입 자산

`assets/prototype/`에는 개발 채널의 네이티브 카드 검증에 사용한 800×400 이미지가 있다.
운영 자산이 아니며 사용자 테스트 후 교체하거나 제거할 수 있다. 이미지에는 동적 문구·브랜드·URL을
넣지 않고, 실제 제목과 설명은 카카오 카드의 텍스트 필드에서 렌더링한다.
