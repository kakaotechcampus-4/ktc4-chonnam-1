# 실험: 카카오 챗봇으로 통화 녹음 파일을 받아 분석할 수 있는가

- 날짜 / 담당: 2026-10-27 / (작성자)
- 질문:
  1. 카톡에서 녹음 파일(m4a 등)을 보내면 스킬 서버가 호출되는가? 어느 블록으로 가는가?
  2. 파일이 **다운로드 가능한 URL** 로 오는가, **파일명만** 오는가? 어느 필드로 오는가?
  3. 그 URL 을 우리 서버가 내려받을 수 있는가? 얼마 동안 유효한가?
  4. 콜백 URL 은 실제로 몇 초까지 쓸 수 있는가? (문서에 5분과 1분이 함께 적혀 있음)
  5. 다운로드 + STT 가 콜백 시간 안에 끝나는가?
- 방법: 아래 "절차"
- 결과 (숫자): (채우기)
- 결론: (채우기 — 맨 아래 판단 기준 참고)

## 준비

프로브 서버: `experiments/voice_upload_probe/app.py`. 운영 봇과 분리돼 있고, 받은 요청을
`probe_logs/` 에 저장한다(`.gitignore` 됨 — 녹음과 사용자 ID 가 들어 있으므로 커밋 금지).

```bash
# repo 루트에서
pip install -r requirements.txt
uvicorn experiments.voice_upload_probe.app:app --port 8000
ngrok http 8000          # 다른 터미널. https 주소를 복사
```

STT 시간까지 재려면 `pip install faster-whisper` (선택). 설치 안 하면 STT 는 건너뛰고
다운로드까지만 잰다.

환경변수 (선택):

| 이름 | 기본값 | 용도 |
|---|---|---|
| `PROBE_MEDIA_HOSTS` | `kakaocdn.net,kakao.com,daumcdn.net,kakaousercontent.com` | 내려받을 호스트. 로그의 `[MEDIA HOST]` 가 목록 밖이면 추가 |
| `PROBE_CALLBACK_DELAY` | `0` | 콜백을 일부러 늦출 초. 콜백 만료 측정용 |
| `PROBE_WHISPER_MODEL` | `small` | faster-whisper 모델 |

## 오픈빌더 설정 (테스트 전용 봇)

운영 스미싱 봇을 건드리지 않도록 **테스트용 채널 + 봇을 새로 만든다.**

1. 스킬 등록: URL `https://<ngrok주소>/skill`
2. **폴백 블록**에 스킬 연결 → 봇 응답 "스킬데이터 사용". 파일은 어떤 발화 패턴에도
   안 걸리므로 폴백으로 갈 가능성이 높다.
3. 폴백 블록 설정에서 **Callback 사용 ON** (AI 챗봇 전환 신청이 돼 있어야 보임).
4. **배포**한 뒤 실제 카톡 앱으로 테스트한다. 관리자센터 봇테스트는 콜백을 제대로
   지원하지 않는다.

## 절차

각 단계에서 카톡 응답 화면과 `probe_logs/*-payload.json`, `*-report.json` 을 남긴다.

| # | 보낼 것 | 확인할 것 |
|---|---|---|
| 1 | 텍스트 "안녕" | 서버가 호출되는지, 콜백 대기 문구가 뜨는지 (기본 동작 확인) |
| 2 | 사진 1장 | utterance 에 CDN URL 이 오는지 (알려진 동작 — 대조군) |
| 3 | **통화 녹음 파일** (갤럭시 통화 녹음 m4a, 1분 내외) | 서버 호출 여부, URL 위치(`$.userRequest.utterance`? `$.action.params...`?), 다운로드 결과 |
| 4 | 같은 파일을 "파일" 메뉴로 첨부 vs 공유하기로 전송 | 전송 경로에 따라 payload 가 다른지 |
| 5 | 긴 녹음 (5~10분) | 파일 크기, 다운로드·STT 시간 |
| 6 | 3번 payload 의 URL 을 10분·1시간 뒤 브라우저로 열기 | URL 유효 시간 |
| 7 | `PROBE_CALLBACK_DELAY=30`, `55`, `90`, `240` 으로 각각 재시작 후 녹음 전송 | 카톡에 결과가 오는 마지막 지연값 = 실제 콜백 한도 |

로그 읽는 법:
- 서버 콘솔 `[PROBE] urls=N callback=True` — N 이 0 이면 URL 이 안 온 것.
- `[MEDIA HOST] <경로> <호스트>` — 오디오가 들어온 필드와 호스트.
- `*-report.json` 의 `downloads[].status / content_type / bytes / seconds` — 실제로 받아졌는지.
- `callback_result.status` 가 `SUCCESS` 가 아니면 콜백 만료·실패.
- 서버가 아예 호출되지 않으면(로그 없음) 카톡 클라이언트에서 파일 전송 자체가 막힌 것.

## 결과 기록

| # | 서버 호출 | URL 위치 | 호스트 | Content-Type | 크기 | 다운로드(초) | STT(초) | 비고 |
|---|---|---|---|---|---|---|---|---|
| 1 | O | — | — | — | — | — | — | 폴백 블록, callbackUrl 없음 (Callback 미설정) |
| 2 | O | `utterance`, `params.media.url` (`media.type=image`) | talk.kakaocdn.net | — | — | — | — | 같은 주소가 두 필드에 옴. 서명 URL, `expires` 약 2029-10 |
| 3 | O | `utterance` 만 (`params.media` 없음) | talk.kakaocdn.net | `etc/m4a` | 5.39MB (215.8초 녹음) | 1.18 | — | 파일명 `f_….m4a`, 서명 URL. 콜백 없이 측정 |
| 5 | | | | | | | | |

콜백 한도: 30초 ☐ / 55초 ☐ / 90초 ☐ / 240초 ☐ (결과 도착 시 체크)

## 판단 기준

- **URL 이 오고, 받아지고, 콜백 한도 안에 STT 까지 끝남** → 전부 카톡으로 구현.
- **URL 은 오지만 콜백 한도를 넘김** → 받자마자 "분석 중" 응답 + 결과 저장, 지금
  스미싱 흐름의 "결과 확인" 조회 폴백(`handle_check_result`)과 같은 방식으로 꺼내 준다.
- **파일명만 오거나 호출 자체가 안 됨** → 업로드는 웹, 카톡은 진입·안내만.

## 함께 확인할 것

- 멘토님께 실제 오디오 수신 사례·내부 문서가 있는지 여쭤보기.
- 공식 답변은 챗봇 관리자센터 고객센터(cs.kakao.com) 문의.
- 통화 녹음에는 상대방 음성·개인정보가 있다. 약관 동의 문구에 녹음 파일 전송·보관이
  포함되는지 점검. `probe_logs/` 는 실험이 끝나면 지운다.
