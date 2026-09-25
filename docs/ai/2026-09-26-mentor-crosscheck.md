# PR #24 멘토 피드백 대조 및 AI 구현 범위

수정 전 기준: `dee85cb`, AI 테스트 627개 통과. 사용자 지정 브랜치 `refactor/scenario-message-test-ai-temp`에서 진행한다.

| 피드백 | 이번 AI 반영 | 별도 협의·남는 한계 |
| --- | --- | --- |
| [official 의미부터 합의](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505850) | official을 최종 결과로 복사하지 않고 부분 answer와 종합 | 점수 기준 통과를 실제 공식 도메인 검증으로 바꾸지 않음 |
| [공식 분기에서 근거 소실](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505877) | 조기 반환 제거, 전달된 문자·페이지 결과 보존 | BE가 아예 전달하지 않은 자료는 AI가 복구할 수 없음 |
| [판정·완료·안내 상태 구분](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505864) | answer true/false/null, result는 사용자 합의대로 신뢰만 true | official 미확인 상태, 문자열 result, FE 표시 변경은 범위 밖 |
| [실패를 악성 근거로 안내 금지](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505887) | 실패 문구 수정, 실패 answer=null, 확보한 근거 유지 | BE·FE의 기존 false 표시 문구는 후속 연동 필요 |
| [수집 실패·미실행·부분 자료 구분](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505884) | 전달된 failure와 부분 자료 보존, 누락을 수집 실패로 지어내지 않음 | 수집기가 전달하지 않은 상태를 추정하지 않음 |
| [수신·파싱 시간과 복잡도 제한](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505911) | AI 입력 사전 검사·깊이·협력적 시간 예산·출력 상한 | BE의 수신 byte 제한·AWS 브라우저 자원 통제는 별도 |
| [필요 자료와 부분 상태 보존](https://github.com/kakaotechcampus-4/ktc4-chonnam-1/pull/24#discussion_r4102505916) | 미검사 꼬리 근거 제외, DOM 인계 요구사항 문서화 | 원격 스키마·수집기·마스킹 구현은 이번 변경 제외 |

## 구현 판단

- 멘토는 구체적인 result 논리식이나 제한 숫자를 지정하지 않았다. 종합 boolean·tri-state answer는 이후 사용자가 정한 정책이다.
- 64단계·100ms·검사 JSON 256KiB·LLM 입력 128KiB는 초기 제안 상한이다. 운영 성능·안전성을 보증하는 실측값이 아니다.
- 빈 부분 객체는 조립 시 누락 이유를 부여한다. 일부 값이 있는 실패 부분에는 실패 설명을 요구해 null을 정상 생략으로 오인하지 않게 한다.
- 문서 예시가 내부 helper 형태를 과하게 고정하면 동등한 외부 동작을 검증하는 테스트로 바꾼다. 근거 검증과 취소 전파는 보존한다.
- AI 코드·테스트·AI 계약 및 인계 문서만 수정한다. main.py, backend/, AWS, Docker 설정, legacy decide()는 변경하지 않는다.
- result 타입은 같아도 의미가 달라진다. 기존 URL-only 공식 호출은 누락으로 result=false가 되며, 전체 서비스 연동 완료라고 보고하지 않는다.

## 검증 단위

1. 결과 계약: 타입·부분 생성·최종 조립·공개 함수는 하나의 계약으로 묶어 구현 후 독립 리뷰한다.
2. HTML 검사: 원문 경계·시간·크기 제한과 실패 통합을 하나의 검사 흐름으로 구현 후 독립 리뷰한다.
3. 두 변경을 합친 전체 AI 테스트와 최종 독립 리뷰를 수행한다.

## 독립 사전 검토 반영

- 최종 AnalysisResponse 직접 역직렬화 경로에서도 answer=null이면 비어 있지 않은 실패 설명을 요구한다. 조립 함수의 정규화만으로 보호하지 않는다.
- FALLBACK PageAnalysis에도 이미 검증된 브랜드·분류가 있으면 보존한다. UNKNOWN 기본값만 미확보로 전환한다. 기존 검증값 우선 보존 회귀 테스트를 유지한다.
- 현재 main.py는 수집기 미연결을 COLLECTION_FAILED로 전달한다. AI는 전달받은 이유를 유지하지만 실제 실패와 미실행을 상류 정보 없이 재구별할 수 없다. 원격 상태 필드 합의는 미완료다.
- 수집 DOM·URL·지시문은 저장 후에도 불신 입력이다. 관리자 화면·로그 도구에서도 출력 위치에 맞게 이스케이프하고 HTML로 실행하지 않도록 인계 문서에 기록한다.
- 혼합 CR/LF 입력에서 기존 splitlines 기반 오프셋이 HTMLParser의 LF 기준 행 위치와 어긋나는 문제를 재현했다. 입력 `prefix\\rbreak\\n<form>...`의 form 시작은 13이나 현재 7로 반환된다. HTML 원문 근거 경계 수정에 함께 포함한다.
