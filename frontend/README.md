# frontend

챗봇 사용자가 보는 화면입니다. 두 폴더로 나뉩니다.

- `chatbot-web/`: 챗봇 카드 버튼으로 여는 1회용 웹페이지(Vite + React). 결과 상세 페이지와 통화 녹음 업로드 페이지가 있습니다([ADR 0006](../docs/adr/0006-one-time-result-web.md)).
- `kakao-templates/`: 챗봇 카드 JSON(선언형). 렌더링은 저장소 맨 위 `services/kakao_card_renderer.py`가 `cards/`를 직접 읽어서 합니다.

## 이 폴더가 하지 않는 것

- **판단하지 않는다**: 판정, 해석, 근거 보강은 전부 AI 파트의 결과이고, 이 폴더는 받은 것을 배치할 뿐입니다. 관측이 비었을 때 화면이 해석을 지어내는 경로가 있으면 버그입니다.
- **BE 말고는 어디에도 보내지 않는다**: 결과 웹은 받기만 하고, 업로드 웹만 입장권을 붙여 녹음을 BE에 보냅니다. 외부 글꼴, 이미지, 분석 도구도 불러오지 않습니다.
- **대상 페이지에 접근하지 않는다**: 의심 URL을 `<a>`로 만들거나, 미리보기, 썸네일, og:image를 불러오지 않습니다. 사용자 브라우저가 그 페이지에 닿는 순간 제품 전제가 깨집니다.
- **안내 문구는 정해진 것만 쓴다**: 카드 문구는 `docs/designs/chatbot-copy.md`가 기준입니다. 개인정보 안내는 시험 운영 문구라 출시 전에 팀과 다시 정합니다.

## 하위 구조

| 폴더               | 역할                                                                    | 자세히                                                 |
| ------------------ | ----------------------------------------------------------------------- | ------------------------------------------------------ |
| `chatbot-web/`     | 결과 상세 페이지(`index.html`), 녹음 업로드 페이지(`upload/index.html`) | [chatbot-web/README.md](chatbot-web/README.md)         |
| `kakao-templates/` | 카드 JSON(`cards/`), 빈칸 목록, 카드 이미지와 프로토타입 자산           | [kakao-templates/README.md](kakao-templates/README.md) |

## 시작하기 전에

- BE 응답 약속은 `chatbot-web/src/contract.ts`에 있습니다. **판정 값에 `safe`는 없습니다**. 없어야 실수로도 안전 보증이 나가지 않습니다.
- BE 응답은 `chatbot-web/src/api/callServer.ts`가 모양을 확인한 뒤에만 화면에 넘깁니다. 컴포넌트가 직접 원본 응답을 받는 경로를 만들지 마세요.
- 실제 스미싱 문자와 URL로 테스트하지 않습니다. 합성 데이터(`chatbot-web/public/mock/`, `chatbot-web/src/api/demoUpload.ts`)와 팀 소유 모형 페이지만 사용합니다.
