# chatbot-web

챗봇 버튼으로 여는 1회용 웹페이지다. 결정은 [ADR 0006](../../docs/adr/0006-one-time-result-web.md)을 따른다.
페이지는 두 장이다. 둘 다 프로토타입이라 서버 없이 가짜 데이터로 화면을 확인한다.

- 결과 상세 페이지 `index.html`: 문자 확인 결과를 자세히 보여 준다.
- 통화 녹음 업로드 페이지 `upload/index.html`(이슈 #74): 녹음을 올리고 분석이 끝날 때까지 기다린 뒤
  "챗봇으로 돌아가 '결과 확인'을 눌러 주세요"로 끝난다. 녹음 결과와 질문은 웹이 아니라 챗봇 카드로 보여 준다.

## 실행

```
cd frontend/chatbot-web
npm install
npm run dev            # 개발 서버. 결과 http://localhost:5173/#t=demo-caution
                       #           업로드 http://localhost:5173/upload/#t=demo-ok
npm test               # 테스트 (Vitest)
npm run lint           # ESLint
npm run format         # Prettier로 정리
npm run build          # 타입 검사 후 dist/ 빌드. 커밋 전에 돌려 빌드가 깨지지 않는지 본다
```

| 주소 (`#` 뒤)       | 화면                                        |
| ------------------- | ------------------------------------------- |
| `#t=demo-caution`   | 주의가 필요한 문자 (청소행정과 과태료 사칭) |
| `#t=demo-lookalike` | 공식 주소와 다름 (택배 사칭, 앱 설치 요구)  |
| `#t=demo-uncertain` | 판단하기 어려워요 (강조할 근거 없음)        |
| `#t=demo-expired`   | 열람 시간이 지남                            |
| `#t=demo-not-found` | 결과를 찾을 수 없음 (서버 재시작 등)        |
| (없음)              | 입장권 없음                                 |

`#t=demo-…` 입장권이면 `serverFor`가 진짜 서버 대신 가짜 서버(`src/api/demoServer.ts`)를 고른다. 결과 페이지의 가짜 서버는 `public/mock/…json`을 읽는다. 그 밖의 입장권은 `GET /api/result`에 `Authorization: Bearer <입장권>`으로 보낸다.
업로드 페이지(`/upload/` 뒤에 붙인다)의 가짜 입장권은 아래와 같다. 올리기와 기다리기는 파일로 흉내 낼 수 없어서
같은 가짜 서버가 응답한다. 아무 녹음 파일이나 골라 보내면 된다.

| 주소 (`#` 뒤)     | 화면                                                         |
| ----------------- | ------------------------------------------------------------ |
| `#t=demo-ok`      | 보내기 → 분석 중(6초) → 분석이 끝났어요                      |
| `#t=demo-fail`    | 분석 중 → 분석하지 못했어요                                  |
| `#t=demo-slow`    | 분석 중 → 시간이 걸리고 있어요 (실제 3분을 10초로 줄임)      |
| `#t=demo-offline` | 처음 보내기는 실패, 고른 파일을 그대로 두고 다시 보내면 성공 |
| `#t=demo-expired` | 보내기 → 올리기 화면을 열 수 없어요 (입장권 만료)            |
| `#t=demo-used`    | 보내기 → 이미 올린 녹음이 있어요                             |
| (없음)            | 올리기 화면을 열 수 없어요                                   |

프로토타입이라 빌드 결과에도 가짜 데이터와 가짜 응답이 들어간다. 실제 API 연결이 끝나면 `src/api/server.ts`의 `serverFor`에서 데모 갈래를 막아 뺀다.

## 구조

```
index.html               결과 페이지 틀. 자동 링크·Referer·검색 노출을 끈다
upload/index.html        업로드 페이지 틀. 머리는 결과 페이지와 같다
src/
├─ main.tsx, upload.tsx  두 페이지의 진입점. App.tsx는 같이 쓰는 바깥 틀
├─ pages/ResultPage.tsx  불러오는 중 / 오류 / 결과를 고른다
├─ pages/UploadPage.tsx  파일 고르기 / 분석 중 / 끝났어요 / 오류를 고른다
├─ components/           OriginalMessage(원문 + 근거 강조 + 설명) · LineList · ErrorState · Notice · Section
│                        (+ 각 .module.css). 한 곳에서만 쓰는 칸은 페이지 안에 둔다
├─ hooks/                useHashToken(입장권 읽기) · useResult(불러오기, useReducer)
│                        · useUpload(보내기와 상태 묻기, useReducer)
├─ api/                  server(웹이 BE에 바라는 일 + serverFor: 입장권으로 진짜·가짜 서버 고르기)
│                        · httpServer(진짜 서버) · demoServer(가짜 서버)
│                        · callServer(입장권 붙여 요청, 상태 코드와 응답 모양 확인)
├─ lib/                  defang(주소 무해화) · markMessage(근거 표시) · token · time · audioFile(파일 검사)
├─ contract.ts           BE 응답 모양(zod)과 거기서 뽑은 타입 (FE 제안 계약)
└─ **/*.test.ts(x)       테스트
public/mock/             가짜 결과
dist/                    빌드 결과. 커밋하지 않는다 (아래 "배포")
```

컴포넌트는 이름 붙여 내보낸다(`export function OriginalMessage`). 스타일은 컴포넌트 옆의 CSS Module(`OriginalMessage.module.css`)에 둔다.

## 배포

`dist/`는 커밋하지 않는다(2026-10-21). 저장소 `.gitignore`가 막고 있고, 커밋하면 빌드를 잊었을 때 옛 화면이 나가고
브랜치를 합칠 때 빌드 파일끼리 충돌한다. 메인 서버(Render)가 배포할 때 빌드 명령 앞에서
`cd frontend/chatbot-web && npm ci && npm run build`를 돌리고, BE가 `dist/`를 정적 폴더로 내보낸다(BE와 합의 전).
Render의 Python 서비스에서 Node를 쓸 수 있는지는 아직 확인하지 않았다.

## 기술 스택과 고른 이유

| 영역      | 선택                                                             | 비교한 것                                 | 고른 이유                                                                                                       |
| --------- | ---------------------------------------------------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| UI        | React 19                                                         | 함수로 DOM 조립(직접 작성)                | 화면이 두 장(결과·업로드)이 되고 공통 부품이 생겼다. 함수로 나눠 보니 React를 흉내 내는 꼴이었다                |
| 언어      | TypeScript 6.0.3                                                 | JavaScript, TypeScript 7                  | BE 응답 모양을 타입으로 지킨다. 7은 typescript-eslint가 아직 지원하지 않아(6.1 미만) 6.0.3을 쓴다               |
| 빌드      | Vite                                                             | —                                         | React 공식 시작 도구. 테스트(Vitest)와 설정을 같이 쓴다                                                         |
| 스타일    | CSS Modules                                                      | 컴포넌트별 일반 CSS, Tailwind             | 컴포넌트 옆에 CSS를 두면서 이름 충돌을 자동으로 막는다. 추가 설치가 없다                                        |
| 린트·포맷 | ESLint(typescript-eslint, react-hooks, react-refresh) + Prettier | Biome, Oxlint                             | React 훅 규칙이 가장 촘촘하다. 팀이 가장 익숙하다                                                               |
| 테스트    | Vitest + Testing Library                                         | Jest                                      | Vite 설정을 그대로 쓴다. 사용법은 Jest와 같다                                                                   |
| 불러오기  | `useEffect` + `useReducer`                                       | React 19 `use()`+Suspense, TanStack Query | 요청 한 번이라 가장 단순하다. 상태가 불러오는 중·준비됨·오류로 분명히 나뉜다                                    |
| 응답 검사 | zod 4 작은 판(`zod/mini`)                                        | 손으로 짠 검사 함수, zod 기본판           | 모양을 한 번 적으면 타입과 실행 중 검사가 같이 나온다. 기본판은 gzip +22.6KB, 작은 판은 +5.8KB라 작은 판을 쓴다 |

TypeScript 타입은 실행할 때 서버 응답을 검사하지 않는다. 그래서 `callServer`가 `contract.ts`의 zod 모양으로 응답을 확인하고,
어긋나면 오류로 돌려준다. 요청 함수는 오류를 던지지 않고 `{ ok, data }` 또는 `{ ok, error }`를 돌려줘서, 받는 쪽이 타입만 보고 어떤 오류가 올지 안다.
2026-10-16에는 응답이 하나라 zod가 과하다고 보고 손으로 검사했다. 업로드가 더해져 응답이 셋이 되자 타입과 검사 코드를
두 번씩 적게 돼서 2026-10-22에 zod로 바꿨다.

처음(2026-10-15)에는 화면이 한 장이라 React 없이 TypeScript만 쓰기로 했다. 결과 웹이 더해진 2026-10-16에 위처럼 바꿨다.

## 테스트

틀리면 사용자가 위험해지는 함수는 테스트를 먼저 쓰고 맞췄다(TDD).

| 대상                                 | 확인하는 것                                                                                                                                                                                                                               |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `defang`                             | `http`·`https`·대문자·스킴 없는 주소·IP 주소·포트·경로 속 주소(`?next=https://…`)를 모두 바꾸고, 숫자·글은 건드리지 않는다                                                                                                                |
| `markMessage`                        | 원문과 인용을 같이 무해화해 찾는다. 원문에 없거나 겹친 인용은 칠하지도 설명하지도 않고, 번호는 인용 순서대로                                                                                                                              |
| `parseToken`                         | `#t=`만 읽고, 비었으면 null                                                                                                                                                                                                               |
| `serverFor`                          | 데모 입장권이면 가짜 서버를 골라 BE에 요청하지 않고, 아니면 진짜 서버를 골라 3분까지 기다린다. 아래 세 요청은 진짜 서버로 확인한다. 대답이 없으면 조회는 10초, 올리기는 2분 뒤 끊는다                                                     |
| `fetchResult`                        | 입장권은 헤더로, `no-store`로 요청한다. 401·410 만료, 404 없음, 서버 오류와 인터넷 끊김은 실패. JSON이 아니거나 모르는 `tone`·`kind`, 빠진 필드, 잘못된 타입이면 실패. 모르는 필드는 버린다                                               |
| `resultReducer`                      | 불러오는 중 → 준비됨 / 오류                                                                                                                                                                                                               |
| `OriginalMessage`                    | 원문의 `<script>`·`<img>`가 실행되지 않고 글자로 보인다. 링크(`<a>`)를 만들지 않는다                                                                                                                                                      |
| `ResultPage`                         | 입장권 없음·만료·결과 없음(404)·응답 오류 화면. 입장권은 주소가 아니라 헤더로 보내고 `no-store`로 요청한다                                                                                                                                |
| `checkAudioFile`                     | m4a·mp3·wav·webm만, 25MB까지, 빈 파일 거절. 휴대폰이 파일 종류(MIME)를 비워 보내도 확장자로 판단한다                                                                                                                                      |
| `uploadRecording`, `fetchCaseStatus` | 응답 코드를 오류 종류로 바꾼다(400 이유, 401·410 만료, 409 이미 올림, 그 밖은 보내기 실패). 모르는 상태 값은 거절한다                                                                                                                     |
| `UploadPage`                         | 고르기 → 보내기 → 분석 중 → 끝났어요. 질문 대기도 끝으로 본다. 분석 실패, 3분 넘김(묻기 멈춤, 대답이 아예 없어도), 묻기 한 번 실패 뒤 재시도, 기다리는 중 만료, 보내기 실패 뒤 같은 파일로 다시 보내기, 서버 거절, 이미 올림, 입장권 만료 |

화면 배치와 색은 테스트하지 않고 눈으로 확인한다.

## 지키는 것

- **판단하지 않는다.** 결론·문장은 BE가 카드와 같은 규칙으로 만든 것을 그대로 쓴다. 강조는 원문에 실제로 있는 인용(`evidence`)만 칠한다.
- **원문 속 주소를 눌리지 않게 바꾼다.** `https://a.example/b` → `hxxps://a[.]example/b`. 전화번호 자동 링크(`format-detection`)와 Referer를 끈다.
- **원문을 HTML로 넣지 않는다.** React가 이스케이프하게 두고 `dangerouslySetInnerHTML`은 ESLint 규칙으로 막았다.
- 외부 글꼴·이미지·스크립트를 불러오지 않는다.

## 업로드 API (FE 제안, BE와 합의 전)

약속은 [이슈 #74 코멘트](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/issues/74)에 제안했다.
합의되면 설계 문서 `docs/designs/agent-loop-plan-v2.md`의 웹 경로 절(§9 B4)에 반영하고, 그 뒤로는 BE 코드가 기준이다.
웹 코드의 기준은 `src/contract.ts`다.

아직 확인하지 않은 것:

- "녹음 파일을 못 찾겠어요" 안내의 경로(갤럭시 `내 파일 → Recordings → Call`, 아이폰 메모 앱 → 파일에 저장)와
  카카오톡 안 브라우저에서 파일 고르기가 열리는지는 실기기로 확인해야 한다.
- 업로드 웹 화면 문구(안내, 오류, 상태)는 지금 `src/pages/UploadPage.tsx`에만 있다. 문구 문서 최신판이 아직 다른 브랜치라,
  두 브랜치를 develop에 합친 뒤 `docs/designs/chatbot-copy.md`의 업로드 안내 카드 아래에 표로 옮긴다.
- 보내기 위 개인정보 2줄은 시험 운영 문구다. 받아 적기를 맡길 서비스와 지우는 시점이 정해지면 팀과 다시 본다.
- "1~2분쯤 걸려요"와 3분 기다리기, 요청 하나를 기다리는 시간(상태와 결과 조회 10초, 녹음 올리기 2분)은 짐작한 값이다.
  실제 분석 시간과 업로드 속도를 보고 맞춘다.

## 조회 API 응답 (FE 제안, BE와 합의 전)

`src/contract.ts`가 기준이다.

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
- 응답에는 `Cache-Control: no-store`를 붙인다. 입장권이 틀렸거나 만료면 401·410, 결과가 사라졌으면(서버 재시작 등) 404를 준다.
  FE는 401·410에는 "다시 열기", 404에는 "문자를 다시 보내기"를 안내한다.
