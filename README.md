# ktc4-team-16
카카오테크 캠퍼스 4기 2단계 팀 프로젝트 — 전남대 1팀

# 🔎 화기니 — 카카오톡 기반 피싱 의심 메시지 검사 챗봇

> **클릭하기 전에, 피싱부터 확인하세요.**  
> 카카오테크캠퍼스 4기 2단계 · 전남대학교 1팀

화기니는 사용자가 받은 의심스러운 문자와 통화 녹음 등을 카카오톡 채널에서 검사하고, 위험 신호와 확인하지 못한 항목을 이해하기 쉬운 카드로 안내하는 피싱 대응 챗봇입니다.

- **서비스 이용 링크:** `http://pf.kakao.com/_ycxoxeX`
- **시연 영상:** `[5분 이내 영상 URL 입력]`
- **발표 자료:** `[발표 자료 URL 입력]`
- **기술문서:** [`docs/README.md`](docs/README.md) / `[제출용 테크스펙 링크]`
- **협업문서:** `[협업문서 링크]`

## 1. 해결하려는 문제

의심스러운 연락을 받은 사용자는 링크를 열거나 개인정보를 전달하기 전에 위험 여부를 빠르게 확인할 필요가 있습니다. 화기니는 별도 앱 설치 없이 카카오톡에서 검사 요청과 결과 확인을 이어갈 수 있도록 설계했습니다.

**대상 사용자:** 문자·통화 등에서 기관 사칭, 금전 요구, 악성 링크가 의심되지만 직접 진위를 판단하기 어려운 사용자.

## 2. 핵심 기능

| 기능 | 설명 |
| --- | --- |
| 의심 문자 분석 | 메시지의 주장과 위험 신호를 분석하고, URL이 있는 경우 추가 검증을 수행합니다. |
| 링크 없는 사칭 문자 분석 | 가족·지인·기관 사칭 등 URL이 없는 메시지도 분석합니다. |
| 통화 녹음 분석 | 음성을 텍스트로 변환하고, 통화 내용의 요구사항과 위험 신호를 분석합니다. |
| 의심 URL 격리 수집 | 별도 격리 수집 서비스에서 웹페이지 정보를 수집해 메인 서버의 직접 접근을 줄입니다. |
| 결과 및 상세 근거 카드 | 카카오톡 카드로 검사 결과, 확인한 사항, 확인하지 못한 사항과 대응 안내를 제공합니다. |
| 후속 질문·응답 | 사용자의 추가 답변을 반영해 일부 문자·통화 검사 결과와 안내를 갱신합니다. |

> **판정 설계 원칙:** LLM은 메시지 이해와 위험 신호 추출에 활용하고, 최종 판정은 정의된 코드 기반 규칙과 검증 결과를 통해 처리합니다. 현재 구현을 자율적 도구 계획·실행형 AI Agent로 소개하지 않습니다.

## 3. 사용 시나리오

1. 사용자가 카카오톡 채널에 의심 문자를 보내거나 지원되는 통화 녹음 파일을 전달합니다.
2. 서버가 요청을 접수하고 분석 작업을 진행합니다.
3. AI 분석, 사례 검색, 필요한 경우 격리 환경의 URL 수집 결과를 조합합니다.
4. 코드 기반 판정 로직을 적용합니다.
5. 카카오톡 결과 카드에서 위험 신호와 상세 근거, 대응 방법을 확인합니다.

**화면 예시:** `[카카오톡 실제 시연 이미지 또는 GIF 삽입]`

## 4. 시스템 구성

```text
사용자
  └─ 카카오톡 채널 / 챗봇
       └─ FastAPI 스킬 서버 (main.py)
            ├─ 입력 검증 및 분석 작업 관리
            ├─ AI 분석 패키지 (ai/)
            │    ├─ 문자·통화 내용 분석
            │    └─ 피싱 사례 검색 및 근거 정리
            ├─ 격리 URL 수집 서비스 (scanner/ → 별도 서버)
            ├─ 코드 기반 최종 판정
            └─ 카카오톡 결과 카드 / 콜백 응답
```

- **백엔드:** Python 3.11+, FastAPI, Uvicorn, httpx
- **AI 분석:** Python 패키지 `ai/`, OpenAI 호환 LLM API, 음성 STT 연동
- **응답 인터페이스:** 카카오톡 챗봇 스킬 API 및 카드 템플릿
- **URL 수집:** 별도 HTTPS 격리 수집 API
- **테스트:** pytest, pytest-asyncio
- **배포:** `[실제 배포 플랫폼 및 서비스 URL 확인 후 기재]`

설계 상세: [`docs/README.md`](docs/README.md), [`docs/isolation-security.md`](docs/isolation-security.md), [`docs/isolation-collector-api.md`](docs/isolation-collector-api.md)

## 5. 로컬 실행

### 사전 준비

- Python **3.11 이상**
- Git
- 외부 LLM·STT·격리 수집 서비스를 이용하려면 해당 서비스의 접근 정보

```bash
git clone https://github.com/kakaotechcampus-4/ktc4-chonnam-1.git
cd ktc4-chonnam-1

python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux: source .venv/bin/activate

python -m pip install -r requirements-dev.txt
```

환경변수 예시 파일을 복사하고 필요한 값을 설정합니다.

```bash
# macOS / Linux
cp .env.example .env
# Windows PowerShell: Copy-Item .env.example .env
```

> `.env`는 예시 설정 파일입니다. 실행 전에 환경변수를 프로세스에 로드하거나 사용 중인 개발 도구의 환경변수 설정에 등록해야 합니다. **실제 API 키·토큰·인증서는 저장소에 커밋하지 마세요.**

개발 환경에서 서버를 실행하는 예시:

```bash
# macOS / Linux
APP_ENV=local python -m uvicorn main:app --reload

# Windows PowerShell
# $env:APP_ENV="local"
# python -m uvicorn main:app --reload
```

서버가 실행되어도 외부 AI·격리 서비스의 인증 정보가 없으면 실제 분석 기능은 제한될 수 있습니다. 카카오톡 연동에는 별도의 채널/챗봇 관리자 설정이 필요합니다.

### 주요 환경변수

| 변수 | 용도 |
| --- | --- |
| `APP_ENV` | `local`, `test`, `production` 등 실행 환경 |
| `LLM_API_KEY` | LLM 및 설정에 따른 STT 서비스 인증 |
| `LLM_BASE_URL`, `LLM_MODEL` | OpenAI 호환 LLM 엔드포인트와 모델 |
| `CLAIMS_MODEL` | 주장 추출용 모델 재정의(선택) |
| `STT_BASE_URL`, `STT_MODEL` | 음성 인식 서비스 설정 |
| `ISOLATION_API_URL`, `ISOLATION_API_TOKEN` | 격리 URL 수집 API 연결 |
| `ISOLATION_API_CA_CERT` | 격리 API 인증서 설정(선택) |
| `KAKAO_SKILL_SECRET` | 운영 환경 카카오 스킬 요청 인증 |
| `KAKAO_CALLBACK_HOSTS` | 허용할 카카오 콜백 호스트 |
| `KAKAO_MEDIA_HOSTS` | 허용할 음성 파일 다운로드 호스트 |

전체 목록은 [`.env.example`](.env.example)을 참고하세요. 운영 환경에서는 허용 호스트를 실제 서비스에서 확인한 값으로 제한해야 합니다.

## 6. 테스트

```bash
python -m pytest -q
```

테스트는 BE API, 입력 검증, 카드 렌더링, AI 분석 관련 로직 등을 대상으로 합니다. 외부 API를 대체하는 오프라인 테스트도 포함됩니다. **최종 통과 건수는 제출 시점의 CI 또는 테스트 실행 결과를 확인해 기재하세요.**

## 7. 배포 및 운영

1. Python 3.11 이상 실행 환경에 저장소를 배포합니다.
2. `requirements.txt`의 의존성을 설치합니다.
3. 운영 환경변수와 외부 AI·STT·격리 API 인증 정보를 설정합니다.
4. ASGI 서버에서 `main:app`을 실행합니다.
5. 카카오톡 챗봇 관리자센터에서 스킬 서버 주소와 `X-Skill-Secret` 헤더를 설정합니다.
6. 실제 카카오톡 채널에서 메시지 전송 → 분석 → 결과 콜백까지 확인합니다.

**배포 환경 확인 후 추가할 항목:** 실제 호스팅 서비스명, 빌드/시작 명령, 공개 서비스 URL, 상태 확인 경로, CI/CD 워크플로 링크.

### 운영 시 유의사항

- 운영 스킬 요청은 `KAKAO_SKILL_SECRET` 검증을 사용합니다.
- 콜백 URL은 별도의 허용 호스트 검증을 거칩니다.
- 의심 URL 수집은 HTTPS 기반 격리 API를 통해 수행합니다.
- 현재 일부 분석 작업 상태는 **인메모리**로 관리되어 서버 재시작 시 유실될 수 있습니다.
- 검사 결과는 보안 판단을 돕는 참고 정보이며, 안전 판정이 실제 발신자나 웹사이트의 안전성을 절대적으로 보증하지는 않습니다.

## 8. 저장소 및 문서

| 경로 | 역할 |
| --- | --- |
| `main.py` | FastAPI 진입점 및 카카오톡 요청 처리 |
| `backend/` | 서버 입력 검증·보안·수명주기 관련 코드 |
| `ai/` | 문자·음성 분석 및 근거 처리 패키지 |
| `scanner/` | 격리 URL 수집 연동 |
| `services/` | 판정 관련 서비스와 카카오톡 결과 카드 구성 |
| `frontend/` | 카카오톡 템플릿 및 카드 리소스 |
| `tests/` | 서버 및 통합 테스트 |
| `docs/` | PRD, 설계, 보안, API 계약, 실험 및 협업 문서 |

- [문서 인덱스](docs/README.md)
- [제품 요구사항](docs/PRD.md)
- [보안 체크리스트](docs/security-checklist.md)
- [코딩·브랜치 규칙](docs/conventions.md)
- [카카오 챗봇 흐름](docs/designs/chatbot-flow.md)

## 9. 팀

**카카오테크캠퍼스 4기 · 전남대학교 1팀**  
- 팀장 : 강성윤
- FE 리드 : 오채민
- BE 리드 : 윤여경
- AI 리드 : 김수호
- 플래너 : 박시현
- 메이커 : 김봄

---

본 프로젝트는 피싱 의심 상황에서 사용자의 판단을 지원하기 위한 팀 프로젝트입니다.
