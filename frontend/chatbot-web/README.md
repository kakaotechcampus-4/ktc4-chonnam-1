# chatbot-web

챗봇 버튼으로 여는 1회용 웹페이지다. 결정은 [ADR 0006](../../docs/adr/0006-one-time-result-web.md)을 따른다.
지금은 **결과 상세 페이지 프로토타입**만 있다. 서버 없이 가짜 데이터(`public/mock/`)로 화면을 확인한다.

## 실행

```
cd frontend/chatbot-web
npm install
npm run dev            # 개발 서버. http://localhost:5173/#t=demo-caution
npm test               # 테스트 (Vitest)
npm run lint           # ESLint
npm run format         # Prettier로 정리
npm run build          # 타입 검사 후 dist/ 빌드. 커밋 전에 꼭 돌린다
```

| 주소 (`#` 뒤)       | 화면                                        |
| ------------------- | ------------------------------------------- |
| `#t=demo-caution`   | 주의가 필요한 문자 (청소행정과 과태료 사칭) |
| `#t=demo-lookalike` | 공식 주소와 다름 (택배 사칭, 앱 설치 요구)  |
| `#t=demo-uncertain` | 판단하기 어려워요 (강조할 근거 없음)        |
| `#t=demo-expired`   | 열람 시간이 지남                            |
| (없음)              | 입장권 없음                                 |

`#t=demo-…`이면 조회 API 대신 `mock/…json`을 읽는다. 그 밖의 입장권은 `GET /api/result`에 `Authorization: Bearer <입장권>`으로 보낸다.
프로토타입이라 `dist/`에도 가짜 데이터가 들어 있다. 실제 배포 전에는 빼야 한다.

## 구조

```
index.html               페이지 틀. 자동 링크·Referer·검색 노출을 끈다
src/
├─ main.tsx, App.tsx     진입점
├─ pages/ResultPage.tsx  불러오는 중 / 오류 / 결과를 고른다
├─ components/           OriginalMessage(원문 + 근거 강조 + 설명) · LineList · ErrorState · Section
│                        (+ 각 .module.css). 한 곳에서만 쓰는 칸은 페이지 안에 둔다
├─ hooks/                useHashToken(입장권 읽기) · useResult(불러오기, useReducer)
├─ api/                  result(조회) · parseResultView(응답 필수 필드 검사)
├─ lib/                  defang(주소 무해화) · markMessage(근거 표시) · token · time
├─ types.ts              조회 API 응답 타입 (FE 제안 계약)
└─ **/*.test.ts(x)       테스트
public/mock/             가짜 결과
dist/                    빌드 결과. 커밋한다 (BE가 이 폴더를 내보낸다)
```

컴포넌트는 이름 붙여 내보낸다(`export function OriginalMessage`). 스타일은 컴포넌트 옆의 CSS Module(`OriginalMessage.module.css`)에 둔다.

## 기술 스택과 고른 이유

| 영역      | 선택                                                             | 비교한 것                                 | 고른 이유                                                                                         |
| --------- | ---------------------------------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------- |
| UI        | React 19                                                         | 함수로 DOM 조립(직접 작성)                | 화면이 두 장(결과·업로드)이 되고 공통 부품이 생겼다. 함수로 나눠 보니 React를 흉내 내는 꼴이었다  |
| 언어      | TypeScript 6.0.3                                                 | JavaScript, TypeScript 7                  | BE 응답 모양을 타입으로 지킨다. 7은 typescript-eslint가 아직 지원하지 않아(6.1 미만) 6.0.3을 쓴다 |
| 빌드      | Vite                                                             | —                                         | React 공식 시작 도구. 테스트(Vitest)와 설정을 같이 쓴다                                           |
| 스타일    | CSS Modules                                                      | 컴포넌트별 일반 CSS, Tailwind             | 컴포넌트 옆에 CSS를 두면서 이름 충돌을 자동으로 막는다. 추가 설치가 없다                          |
| 린트·포맷 | ESLint(typescript-eslint, react-hooks, react-refresh) + Prettier | Biome, Oxlint                             | React 훅 규칙이 가장 촘촘하다. 팀이 가장 익숙하다                                                 |
| 테스트    | Vitest + Testing Library                                         | Jest                                      | Vite 설정을 그대로 쓴다. 사용법은 Jest와 같다                                                     |
| 불러오기  | `useEffect` + `useReducer`                                       | React 19 `use()`+Suspense, TanStack Query | 요청 한 번이라 가장 단순하다. 상태가 불러오는 중·준비됨·오류로 분명히 나뉜다                      |

TypeScript 타입은 실행할 때 서버 응답을 검사하지 않는다. 그래서 `parseResultView`가 필수 필드를 직접 확인한다(zod는 이 규모에서 과하다).

처음(2026-10-15)에는 화면이 한 장이라 React 없이 TypeScript만 쓰기로 했다. 결과 웹이 더해진 2026-10-16에 위처럼 바꿨다.

## 테스트

틀리면 사용자가 위험해지는 함수는 테스트를 먼저 쓰고 맞췄다(TDD).

| 대상              | 확인하는 것                                                                                                        |
| ----------------- | ------------------------------------------------------------------------------------------------------------------ |
| `defang`          | `http`·`https`·대문자·스킴 없는 주소·포트·경로 속 주소(`?next=https://…`)를 모두 바꾸고, 숫자·글은 건드리지 않는다 |
| `markMessage`     | 원문과 인용을 같이 무해화해 찾는다. 원문에 없거나 겹친 인용은 칠하지도 설명하지도 않고, 번호는 인용 순서대로       |
| `parseToken`      | `#t=`만 읽고, 비었으면 null                                                                                        |
| `parseResultView` | 모르는 `tone`·`kind`, 빠진 필드, 잘못된 타입을 거절한다                                                            |
| `resultReducer`   | 불러오는 중 → 준비됨 / 오류                                                                                        |
| `OriginalMessage` | 원문의 `<script>`·`<img>`가 실행되지 않고 글자로 보인다. 링크(`<a>`)를 만들지 않는다                               |
| `ResultPage`      | 입장권 없음·만료·응답 오류 화면. 입장권은 주소가 아니라 헤더로 보내고 `no-store`로 요청한다                        |

화면 배치와 색은 테스트하지 않고 눈으로 확인한다.

## 지키는 것

- **판단하지 않는다.** 결론·문장은 BE가 카드와 같은 규칙으로 만든 것을 그대로 쓴다. 강조는 원문에 실제로 있는 인용(`evidence`)만 칠한다.
- **원문 속 주소를 눌리지 않게 바꾼다.** `https://a.example/b` → `hxxps://a[.]example/b`. 전화번호 자동 링크(`format-detection`)와 Referer를 끈다.
- **원문을 HTML로 넣지 않는다.** React가 이스케이프하게 두고 `dangerouslySetInnerHTML`은 ESLint 규칙으로 막았다.
- 외부 글꼴·이미지·스크립트를 불러오지 않는다.

## 조회 API 응답 (FE 제안, BE와 합의 전)

`src/types.ts`가 기준이다.

```jsonc
{
  "tone": "caution", // lookalike(R1) · caution(R9 주의) · uncertain(R9 판단 보류)
  "headline": "🔎 주의가 필요한 문자예요",
  "action_lines": ["링크를 누르거나 정보를 입력하지 마세요.", "…"],
  "message_text": "[청소행정과] 쓰레기 무단투기 신고가 …", // 문자 원문. 지금 서버는 저장하지 않는다
  "highlights": [{ "quote": "청소행정과", "kind": "claim", "label": "내세운 기관", "note": "…" }], // kind: claim(주장) · request(요구 행동) · signal(검증된 위험 근거)
  "checked": ["문자 내용 (확실한 위험 근거는 찾지 못했어요)"],
  "unchecked": ["실제로 보낸 곳", "…"],
  "guidance": [{ "source": "…", "year": "2025", "text": "…" }],
  "analyzed_at": "2026-10-16T21:12:00+09:00",
  "expires_at": "2026-10-16T21:42:00+09:00",
}
```

- `headline`·`action_lines`·`checked`·`unchecked`는 R1·R9 카드와 같은 문장이다(`docs/designs/chatbot-copy.md`).
- `highlights`는 `message.details`의 `signals`·`doubts`와 `reason.text`의 기관·명분 인용에서 만든다.
- `guidance`는 `find_guidance()` 결과를 자르지 않고 넣는다.
- 응답에는 `Cache-Control: no-store`를 붙인다. 입장권이 없거나 만료면 401·404·410 중 하나를 준다.
