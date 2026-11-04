# frontend

챗봇 카드의 문구, 버튼, 이미지를 둡니다. 웹은 쓰지 않습니다([ADR 0005](../docs/adr/0005-no-detail-web.md)).

## 하위 구조

| 폴더 | 역할 |
| --- | --- |
| `kakao-templates/cards/` | 카드 JSON. BE의 `services/kakao_card_renderer.py`가 직접 읽습니다 |
| `kakao-templates/assets/` | 카드 이미지와 시안 |

빈칸 이름과 블록 ID는 [`kakao-templates/README.md`](./kakao-templates/README.md)에, 문구 원문은 [`docs/designs/chatbot-copy.md`](../docs/designs/chatbot-copy.md)에 있습니다.

## 이 폴더가 하지 않는 것

- **판단하지 않습니다.** 판정과 해석은 AI와 BE의 결과이고, 카드는 받은 값을 보여 줄 뿐입니다.
- **의심 링크를 열 수 있게 하지 않습니다.** 문자 속 URL을 버튼 주소나 이미지로 쓰지 않습니다.

## 작업할 때

- 카드 이미지는 `develop` 브랜치의 GitHub 주소로 불러옵니다. 쓰고 있는 이미지의 이름을 바꾸거나 지우면 실제 카드가 깨집니다.
- 문구를 바꾸면 카드 JSON과 `chatbot-copy.md`를 함께 고칩니다.
- 실제 스미싱 문자와 URL로 테스트하지 않습니다.
