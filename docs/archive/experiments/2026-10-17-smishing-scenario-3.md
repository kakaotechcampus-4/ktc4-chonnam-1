# 기존 기능으로 스미싱 한 사례 검증

대상 흐름: `문자 입력 → 분석 중 → 결과 → 기존 자세히 보기 → 결과 확인`.
AI API·카드·안내 분할 방식·저장소는 그대로 사용한다.

## 입력과 자동 검증

```text
[CJ대한통운] 주소 불일치로 배송이 보류되었습니다. 배송 확인 앱을 설치해 주세요. <테스트 URL>
```

`tests/test_smishing_service_flow.py`는 외부 LLM·urlscan·콜백 HTTP만 대체하고,
실제 내부 분석·카드·작업 저장을 실행한다. 콜백 성공과 전송 실패 모두에서 상세·재조회가
같은 저장 결과를 사용하고 분석을 다시 하지 않는지 확인한다.

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_smishing_service_flow.py tests/test_url_utils.py -q
```

자동 테스트의 `https://parcel-test.example.com/`는 예약 도메인의 가짜 주소로 실제 제출하지 않는다.
실기기에는 팀이 관리하는 무해한 HTTPS 주소와 개인정보 없는 합성 문자를 사용한다.
현재 urlscan 제출은 `public`이다. 자동 검증은 실제 모델 품질·카카오 전달 시간·화면을 입증하지 않는다.

## 승인 후 확인할 설정

현재 대시보드 설정은 미확인이다. Chrome 변경 전 대상과 차이를 보고하고 승인받는다.

- Render: 승인된 `test/render` 배포, 루트에서 `pip install -r requirements.txt`,
  `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1` 실행 여부 확인.
- 환경: LLM 세 키, `URLSCAN_API_KEY`, 스킬 인증값, 실제 관측한 콜백 호스트 확인.
  격리 수집기는 기본 비활성 상태를 유지하고, 비밀값은 기록하지 않는다.
- 카카오: HTTPS `/kakao/skill`, 일치하는 `X-Skill-Secret`, 분석 콜백 사용,
  기존 자세히 보기 블록(`6aad3abfe745d3358bd90c8d`)과 `결과 확인` 인텐트의 스킬 연결 확인.

## 실기기 결과 기록 — 미실행

첫 응답 5초 이내, 최종 결과 수신, R1·R9의 저장된 상세 조회, 확인 시각과 같은 결과의 재조회를
확인하고 배포 커밋·소요 시간·익명화한 화면을 남긴다. R8이 나오면 기존 동작 그대로 기록한다.
페이지 미분석은 미분석으로 표시돼야 한다. 메모리 저장소이므로 진행 중 재배포하지 않는다.
카드 표시나 전화번호 잘림 문제를 발견하면 별도 개선으로 보고하고 임의로 바꾸지 않는다.
